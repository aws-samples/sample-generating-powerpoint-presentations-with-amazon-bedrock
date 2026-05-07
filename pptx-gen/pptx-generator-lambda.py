"""
Lambda handler function to generate pptx using LLM
"""

import json
import logging
import os
import shlex
import subprocess
import traceback
from datetime import datetime
from typing import Any, Dict
import tempfile

from pptx import Presentation
from src.lambda_helpers import (
    fix_slide_json,
    flatten_input_event,
    format_bedrock_response,
    generate_presentation_content,
    save_and_upload_presentation,
    validate_event,
)
from src.slide_generator import process_slide
from src.utils import generate_bedrock_image

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

lambda_root = os.environ["LAMBDA_TASK_ROOT"]
pptx_base_template = lambda_root + "/templates/pptx_base_template.pptx"
default_bkg = lambda_root + "/templates/default_bkg.jpg"


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler to generate PowerPoint presentations using Amazon Bedrock
    """
    logger.info("Starting presentation generation")

    try:
        # Initialize and validate
        flattened_event = flatten_input_event(event)
        ss, error_response = validate_event(flattened_event)
        if error_response:
            return format_bedrock_response(event, error_response)

        # Use /tmp for Lambda temporary storage
        start_time = datetime.now()

        json_content_list, error_response = generate_presentation_content(ss)
        if error_response:
            return format_bedrock_response(event, error_response)

        # Fix individual slide JSONs
        json_content_list = [fix_slide_json(slide_dict, ss) for slide_dict in json_content_list]

        # Create presentation from validated JSONs
        prs = Presentation(pptx_base_template)

        lambda_tmp = tempfile.mkdtemp()

        # Generate background image if needed
        if ss.selected_generate_bkg:
            generate_bedrock_image(
                img_prompt=ss.bkg_prompt,
                current_slide_format_json={
                    "image_height": 768,
                    "image_width": 1152,
                },
                cwd=lambda_tmp,
                bkg="_bkg",
                high_res_images=ss.high_res_images,
            )
            ss.n_gen_images = ss.n_gen_images + 1

        # Process each slide
        i_slide_col = 0
        for slide_data in json_content_list:
            i_slide_col = process_slide(prs, ss, json_content_list, slide_data, lambda_tmp, default_bkg, i_slide_col)

        # Save & upload presentation
        output_filename, presigned_url = save_and_upload_presentation(prs, ss)

        # Generate thumbnails
        if ss.generate_thumbnails:
            ss.output_dir_images = ss.output_path.replace(".pptx", "/")
            os.makedirs(ss.output_dir_images, exist_ok=True)
            unoconv_cmd = str("unoconv -o " + ss.output_dir_images + "  -f html " + ss.output_path)
            # nosemgrep: dangerous-subprocess-use-audit, input not controllable by an external resource / no user input
            subprocess.run(shlex.split(unoconv_cmd), shell=False)

        # Calculate stats and duration
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        input_token_cost_cent = ss.n_input_tokens * ss.llm_input_token_price * 100
        output_token_cost_cent = ss.n_output_tokens * ss.llm_output_token_price * 100
        image_cost_cent = ss.n_gen_images * ss.gen_images_price_cents
        total_cost_cent = input_token_cost_cent + output_token_cost_cent + image_cost_cent
        # Print statistics
        logger.info("PRESENTATION GENERATION COMPLETED!")
        logger.info(f"TOTAL NUMBER INPUT TOKEN:      {ss.n_input_tokens} (¢ {round(input_token_cost_cent,3)})")
        logger.info(f"TOTAL NUMBER OUTPUT TOKENS:    {ss.n_output_tokens} (¢ {round(output_token_cost_cent, 3)})")
        logger.info(f"TOTAL NUMBER IMAGES GENERATED: {ss.n_gen_images} (¢ {round(image_cost_cent, 3)})")
        logger.info(f"TOTAL COST IN CENTS OF $:      ¢ {round(total_cost_cent, 2)}")
        logger.info(f"TOTAL PPTX GENERATION TIME:    {duration} seconds")

        response_body = {"filename": output_filename, "url": presigned_url}
        payload = format_bedrock_response(event, response_body)
        return payload

    except Exception as e:
        error_details = f"Exception: {str(e)} - {traceback.format_exc()}"
        logger.error(f"ERROR: {error_details}")
        return {"error": f"Internal error: {str(e)}"}
