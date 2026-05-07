# -*- coding: utf-8 -*-
"""
Functions for generating slides
"""

import ast
import json
import logging

from pptx import Presentation, slide, util
from src.prompt import agenda_prompt, summary_image_prompt
from src.session_state_template import SessionState
from src.utils import generate_bedrock_image, generate_text, invoke_llm_text, validate_text_length

from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Pt

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def add_slide_notes(slide: slide.Slide, slide_data: dict, debug_mode: bool = False):
    """Add speaker notes and optional debug info to slide"""
    logger.info(f"Adding slide notes:")
    logger.info(f"Debug mode: {debug_mode}")
    logger.info(f"Slide data available: {bool(slide_data)}")

    notes_slide = slide.notes_slide
    text_frame = notes_slide.notes_text_frame

    # Add speaker notes
    speaker_notes = slide_data.get("speaker_notes", "")

    # Format the notes content
    notes_content = f"Speaker Notes:\n{speaker_notes}\n"

    # Optionally add debug information
    if debug_mode:
        logger.info("Adding debug info to notes")
        debug_info = f"\n--- Debug Info ---\nSlide Format: {slide_data['slideFormat']}\n"
        debug_info += f"Full slide data:\n{json.dumps(slide_data, indent=2)}"
        notes_content += debug_info
    else:
        logger.info("Debug mode is off - not adding debug info")

    text_frame.text = notes_content
    logger.info(f"Notes content length: {len(notes_content)}")


def verify_placeholders(slide: slide.Slide, format_json: dict):
    """Verify all required placeholders exist in the slide"""
    logger.info("Starting verify_placeholders")
    logger.info(f"Format JSON received: {json.dumps(format_json, indent=2)}")

    available_placeholders = {shape.placeholder_format.idx: shape.name for shape in slide.placeholders}

    # Define required placeholders based on slide type
    required_placeholders = {}

    # Base placeholders every slide should have
    if "title_placeholder" in format_json:
        required_placeholders["title"] = format_json["title_placeholder"]

    # Optional placeholders based on slide type
    if "subtitle_placeholder" in format_json:
        required_placeholders["subtitle"] = format_json["subtitle_placeholder"]

    if "text_placeholder" in format_json:
        required_placeholders["text"] = format_json["text_placeholder"]

    logger.info("Placeholder Analysis:")
    logger.info(f"Format JSON keys: {format_json.keys()}")
    logger.info(f"Required placeholders: {required_placeholders}")
    logger.info(f"Available placeholders: {available_placeholders}")

    # Check for missing placeholders
    missing_placeholders = []
    for name, idx in required_placeholders.items():
        if idx not in available_placeholders:
            missing_placeholders.append(f"{name} (index {idx})")

    if missing_placeholders:
        logger.warning(f"Missing placeholders: {', '.join(missing_placeholders)}")

def adjust_title_spacing(slide: slide.Slide):
    """Adjust spacing between title and subtitle."""
    title_shape = None
    subtitle_shape = None
    
    for shape in slide.placeholders:
        if shape.placeholder_format.type == PP_PLACEHOLDER.TITLE:
            title_shape = shape
        elif shape.placeholder_format.type == PP_PLACEHOLDER.SUBTITLE:
            subtitle_shape = shape
    
    if title_shape and subtitle_shape:
        # Add some spacing between title and subtitle
        subtitle_shape.top = title_shape.top + title_shape.height + Pt(12)

def create_title_slide(
    slide: slide.Slide,
    validated_slide_json_content: dict,
    current_slide_format_json: dict,
    ss: SessionState,
    cwd: str,
    default_bkg: str,
    prs: Presentation,
):
    """Create a title slide"""
    # Slide title
    title = slide.placeholders[current_slide_format_json["title_placeholder"]]
    title.text = validated_slide_json_content["title"]
    adjust_text_fit(title)
    # Slide subtitle
    subtitle = slide.placeholders[current_slide_format_json["subtitle_placeholder"]]
    subtitle.text = validated_slide_json_content["subtitle"]
    adjust_text_fit(subtitle)
    # Slide Author Name and Title
    if ss.customize_contact_info is True:
        full_name = slide.placeholders[current_slide_format_json["full_name_placeholder"]]
        full_name.text = ss.your_full_name
        job_title = slide.placeholders[current_slide_format_json["job_title_placeholder"]]
        job_title.text = ss.your_title + "\n" + ss.your_company
    # Customize background picture
    apply_background_image(slide, ss, cwd, default_bkg, prs)

    # Add original input before generated output
    notes_slide = slide.notes_slide
    text_frame = notes_slide.notes_text_frame
    text_frame.text = "Input topic/text:\n" + ss.topic + "\n\nGenerated content:\n" + text_frame.text

    return slide


def create_agenda_slide(
    prs: Presentation, ss: SessionState, json_content_list: list, current_slide_format: str = "Agenda"
):
    """Create an agenda slide"""
    logger.info(f"CREATING AGENDA SLIDE. FORMAT: {current_slide_format}")
    current_slide_format_json = ss.slides_format_json[current_slide_format]
    layout_slide_idx = current_slide_format_json["layout_slide"]
    slide_layout = prs.slide_layouts[layout_slide_idx]
    slide = prs.slides.add_slide(slide_layout)

    # Slide title
    title = slide.placeholders[current_slide_format_json["title_placeholder"]]
    title.text = current_slide_format_json["agenda_title"]

    agenda_items = []
    agenda_slide_json_fix_attempts = 0
    max_agenda_slide_json_fix_attempts = 3
    while type(agenda_items) != "str" and agenda_slide_json_fix_attempts < max_agenda_slide_json_fix_attempts:
        agenda_slide_json_fix_attempts = agenda_slide_json_fix_attempts + 1
        result_agenda_items, usage = invoke_llm_text(
            prompt=agenda_prompt(SLIDE_TITLES=[item["title"] for item in json_content_list]),
            model_id=ss.chosen_llm,
        )
        ss.n_input_tokens = ss.n_input_tokens + usage["input_tokens"]
        ss.n_output_tokens = ss.n_output_tokens + usage["output_tokens"]
        try:
            agenda_items = ast.literal_eval((result_agenda_items[0])["text"])["agenda_points"]
        except ValueError:
            logger.error("FAILED TO GENERATE AGENDA")
            agenda_items = []

    # Slide main text
    main_text = slide.placeholders[current_slide_format_json["text_placeholder"]]
    try:
        agenda_items_text = agenda_items.replace("*** ", "\n").replace("- ", "").rstrip().lstrip()
    except ValueError:
        agenda_items_text = str(agenda_items).replace("[", "").replace("]", "")
    if len(agenda_items_text) == 1:
        agenda_items_text = agenda_items_text.replace(", ", "\n")

    main_text.text = agenda_items_text

    # Debug info
    logger.info(f"layout slide index: {layout_slide_idx}")
    for i, shape in enumerate(slide.placeholders):
        logger.info(f"\t placeholder index: {shape.placeholder_format.idx}, name: {shape.name}")

    return slide


def create_bullet_points_slide(slide: slide.Slide, validated_slide_json_content: dict, current_slide_format_json: dict):
    """Create a slide with bullet points"""
    # Slide title
    title = slide.placeholders[current_slide_format_json["title_placeholder"]]
    title.text = validated_slide_json_content["title"]
    # Slide subtitle
    subtitle = slide.placeholders[current_slide_format_json["subtitle_placeholder"]]
    subtitle.text = validated_slide_json_content["subtitle"]
    # Slide main text
    main_text = slide.placeholders[current_slide_format_json["text_placeholder"]]
    main_text.text = validated_slide_json_content.get("text").replace("*** ", "\n").replace("- ", "").rstrip().lstrip()

    return slide


def create_image_and_text_slide(
    slide: slide.Slide, validated_slide_json_content: dict, current_slide_format_json: dict, ss: SessionState, cwd: str
):
    """Create a slide with image and text"""

    logger.info("Image and Text Slide Creation:")
    logger.info(f"Validated content: {json.dumps(validated_slide_json_content, indent=2)}")
    logger.info(f"Format JSON: {json.dumps(current_slide_format_json, indent=2)}")

    # 1. First handle the title
    try:
        title = slide.placeholders[current_slide_format_json["title_placeholder"]]
        title.text = validated_slide_json_content["title"]
        logger.info(f"Title set: {validated_slide_json_content['title']}")
    except Exception as e:
        logger.error(f"Error setting title: {str(e)}")
        raise

    # 2. Handle the main text before image generation
    formatted_text = ""
    if validated_slide_json_content["slideFormat"] != "Slide with image only":
        try:
            text_content = validated_slide_json_content.get("text", "")
            formatted_text = text_content.replace("*** ", "\n").replace("- ", "").strip()
            logger.info(f"Formatted text content: {formatted_text[:100]}...")  # First 100 chars

            main_text = slide.placeholders[current_slide_format_json["text_placeholder"]]

            # Try multiple methods to set text
            try:
                # Method 1: Direct text assignment
                main_text.text = formatted_text
            except Exception as e1:
                logger.warning(f"Method 1 failed: {str(e1)}")
                try:
                    # Method 2: Using text frame
                    text_frame = main_text.text_frame
                    text_frame.clear()
                    p = text_frame.paragraphs[0]
                    p.text = formatted_text
                except Exception as e2:
                    logger.warning(f"Method 2 failed: {str(e2)}")
                    try:
                        # Method 3: Add new paragraph
                        text_frame = main_text.text_frame
                        text_frame.clear()
                        text_frame.add_paragraph().text = formatted_text
                    except Exception as e3:
                        logger.warning(f"Method 3 failed: {str(e3)}")
                        raise

            logger.info("Text content set successfully")

        except Exception as e:
            logger.error(f"Error setting main text: {str(e)}")
            raise
    else:
        logger.info("Skipping text content for image-only slide")

    # 3. Handle the image last
    if ss.generate_images:
        try:
            image_placeholder = slide.placeholders[current_slide_format_json["image_placeholder"]]
            generate_slide_image(
                validated_slide_json_content,
                current_slide_format_json,
                image_placeholder,
                ss,
                cwd,
                formatted_text if formatted_text else None,
            )
            logger.info("Image generated and placed successfully")
        except Exception as e:
            logger.error(f"Error generating/placing image: {str(e)}")
            raise

    # 4. Verify final state
    logger.info("Final placeholder state:")
    for shape in slide.placeholders:
        try:
            text_content = shape.text if hasattr(shape, "text") else "No text attribute"
            logger.info(f"Placeholder {shape.placeholder_format.idx} ({shape.name}): {text_content[:50]}...")
        except Exception as e:
            logger.error(f"Error checking placeholder {shape.placeholder_format.idx}: {str(e)}")

    return slide


def create_image_only_slide(
    slide: slide.Slide, validated_slide_json_content: dict, current_slide_format_json: dict, ss: SessionState, cwd: str
):
    """Create a slide with image only"""
    # Slide image
    if ss.generate_images:
        image_placeholder = slide.placeholders[current_slide_format_json["image_placeholder"]]
        generate_slide_image(validated_slide_json_content, current_slide_format_json, image_placeholder, ss, cwd)

    return slide


def create_takeaways_slide(slide: slide.Slide, validated_slide_json_content: dict, current_slide_format_json: dict):
    """Create a slide with 4 takeaways"""
    # Slide title
    title = slide.placeholders[current_slide_format_json["title_placeholder"]]
    title.text = validated_slide_json_content["title"]
    # Slide 4 key takeaways
    four_options = validated_slide_json_content.get("text").split("***")
    four_options = filter(None, four_options)
    for i, text_opt in enumerate(four_options):
        if text_opt.rstrip().lstrip() == "":
            continue
        if i > 3:
            continue
        text_opt_placeholder = slide.placeholders[current_slide_format_json["text" + str(i + 1) + "_placeholder"]]
        # print(str(i + 1) + " text_opt_placeholder.text before " + text_opt_placeholder.text)
        text_opt_placeholder.text = text_opt.rstrip().lstrip()
        # print(str(i + 1) + " text_opt_placeholder.text " + text_opt_placeholder.text)

    return slide


def create_thank_you_slide(prs: Presentation, ss: SessionState, cwd: str, default_bkg: str):
    """Create a thank you slide"""
    current_slide_format = "Thank you"
    logger.info(f"CREATING THANK YOU SLIDE. FORMAT: {current_slide_format}")
    current_slide_format_json = ss.slides_format_json[current_slide_format]
    layout_slide_idx = current_slide_format_json["layout_slide"]
    slide_layout = prs.slide_layouts[layout_slide_idx]
    slide = prs.slides.add_slide(slide_layout)

    # Add Thank you! to the slide
    thank_you = slide.placeholders[ss.slides_format_json["Thank you"]["title_placeholder"]]
    thank_you.text = "Thank you!"

    # Slide Author Name
    if ss.customize_contact_info is True:
        try:
            full_name = slide.placeholders[current_slide_format_json["full_name_placeholder"]]
            full_name.text = ss.your_full_name
        except ValueError:
            logger.error("FAILED TO RETRIEVE THE FULL NAME BOX!")

    # Slide Contact
    if ss.customize_contact_info is True:
        try:
            contact_text = slide.placeholders[current_slide_format_json["contact_info_placeholder"]]
            contact_text.text = ss.your_contact_info
        except ValueError:
            logger.error("FAILED TO RETRIEVE THE CONTACT INFO BOX!")

    # Debug info
    logger.info(f"layout slide index: {layout_slide_idx}")
    for i, shape in enumerate(slide.placeholders):
        logger.info(f"\t placeholder index: {shape.placeholder_format.idx}, name: {shape.name}")

    # Add background image
    apply_background_image(slide, ss, cwd, default_bkg, prs)

    return slide


def generate_slide_image(
    validated_slide_json_content: dict,
    current_slide_format_json: dict,
    image_placeholder: int,
    ss: SessionState,
    cwd: str,
    main_text=None,
):
    """Generate an image for a slide"""
    content_for_prompt = (
        main_text
        if main_text and validated_slide_json_content["slideFormat"] == "Slide with image and text"
        else validated_slide_json_content["title"]
    )

    summary_prompt, usage = generate_text(
        prompt=summary_image_prompt(content_for_prompt),
        model_id=ss.chosen_llm,
    )
    ss.n_input_tokens = ss.n_input_tokens + usage["input_tokens"]
    ss.n_output_tokens = ss.n_output_tokens + usage["output_tokens"]

    logger.info("summary_prompt for image generation: " + str(summary_prompt))
    img_prompt = summary_prompt[0].get("text") + ", abstract"

    generate_bedrock_image(
        img_prompt=img_prompt,
        current_slide_format_json=current_slide_format_json,
        image_placeholder=image_placeholder,
        cwd=cwd,
        high_res_images=ss.high_res_images,
    )
    ss.n_gen_images = ss.n_gen_images + 1


def apply_background_image(slide: slide.Slide, ss: SessionState, cwd: str, default_bkg: str, prs: Presentation):
    """Apply a background image to a slide"""
    left = top = util.Inches(0)
    if ss.selected_generate_bkg:
        bkg_img_path = cwd + "/test_image_bkg.jpg"
    else:
        bkg_img_path = default_bkg
    pic = slide.shapes.add_picture(
        bkg_img_path,
        left,
        top,
        width=prs.slide_width,
        height=prs.slide_height,
    )
    cursor_sp = slide.shapes[0]._element
    cursor_sp.addprevious(pic._element)

    return slide

def adjust_text_fit(shape) -> None:
    """Adjust font size to fit text in shape."""
    if not hasattr(shape, 'text_frame'):
        return
        
    text_frame = shape.text_frame
    
    # If text is overflowing
    if text_frame.text and text_frame.auto_size == MSO_AUTO_SIZE.NONE:
        font_size = 40  # Starting font size
        while font_size > 12:  # Minimum font size
            try:
                for paragraph in text_frame.paragraphs:
                    paragraph.font.size = Pt(font_size)
                if not text_frame.text_range.font._element.get('dirty'):
                    break
            except:
                pass
            font_size -= 2

def process_slide(
    prs: Presentation,
    ss: SessionState,
    json_content_list: list,
    slide_data: dict,
    cwd: str,
    default_bkg: str,
    i_slide_col: int,
):
    """Process a single slide based on its format"""
    slide_data = validate_text_length(slide_data)
    current_slide_format = slide_data["slideFormat"]
    logger.info(f"CREATING SLIDE {(i_slide_col + 1)}. FORMAT: {current_slide_format}")
    logger.info(f"Slide data: {slide_data}")

    # Add these debug lines
    logger.info("Template Configuration:")
    logger.info(f"Available formats: {ss.slides_format_json.keys()}")
    logger.info(f"Current format config: {json.dumps(ss.slides_format_json.get(current_slide_format, {}), indent=2)}")

    try:
        current_slide_format_json = ss.slides_format_json[current_slide_format]
    except KeyError:
        logger.error(f"Warning: Unknown slide format '{current_slide_format}', falling back to default")
        current_slide_format = "Slide with bullet points"
        current_slide_format_json = ss.slides_format_json[current_slide_format]

    layout_slide_idx = current_slide_format_json["layout_slide"]
    slide_layout = prs.slide_layouts[layout_slide_idx]
    slide = prs.slides.add_slide(slide_layout)

    # Add this line to get debug mode from SessionState
    debug_mode = getattr(ss, "debug_mode", False)

    try:
        verify_placeholders(slide, current_slide_format_json)
    except Exception as e:
        logger.error(f"Warning: Placeholder verification warning (non-critical): {str(e)}")
        # Continue processing the slide even if verification has warnings

    # Process based on slide format
    if current_slide_format == "Title page":
        slide = create_title_slide(slide, slide_data, current_slide_format_json, ss, cwd, default_bkg, prs)

        # If required, create an Agenda after Title slide
        if ss.create_agenda_checkbox is True:
            i_slide_col = i_slide_col + 1
            create_agenda_slide(prs, ss, json_content_list)

    elif current_slide_format == "Slide with bullet points":
        slide = create_bullet_points_slide(slide, slide_data, current_slide_format_json)

    elif current_slide_format == "Slide with image and text":
        slide = create_image_and_text_slide(slide, slide_data, current_slide_format_json, ss, cwd)

    elif current_slide_format == "Slide with image only":
        slide = create_image_only_slide(slide, slide_data, current_slide_format_json, ss, cwd)

    elif current_slide_format == "Slide with 4 takeaways":
        slide = create_takeaways_slide(slide, slide_data, current_slide_format_json)
        adjust_title_spacing(slide)

        # If required, create a Thank You after takeaway slide
        if ss.create_thankyou_checkbox is True:
            i_slide_col = i_slide_col + 1
            create_thank_you_slide(prs, ss, cwd, default_bkg)

    # Debug print
    logger.info(f"Before adding notes:")
    logger.info(f"Session debug_mode: {ss.debug_mode}")

    add_slide_notes(slide, slide_data, ss.debug_mode)

    return i_slide_col + 1
