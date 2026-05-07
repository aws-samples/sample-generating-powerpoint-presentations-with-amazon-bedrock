"""
Session state variables used in pptx-generator-lambda.py
"""

from dataclasses import dataclass
from typing import Optional

from src.template_mapping import template_aws1


@dataclass
class SessionState:
    # Authentication variables
    tenant_id: str = None
    kb_id: str = None  # Knowledge base ID

    # Language model settings
    selected_llm: str = "Sonnet"  # Selected language model name
    chosen_llm: str = "anthropic.claude-3-sonnet-20240229-v1:0"  # Model ID/version
    llm_input_token_price: float = 0.00300 / 1e3  # Price per input token
    llm_output_token_price: float = 0.01500 / 1e3  # Price per output token

    # Presentation topic/content
    topic: str = "Benefits of cloud computing with Amazon Web Services"  # Main topic
    topic_from_text: str = "Benefits of cloud computing with Amazon Web Services"  # Copy of topic
    n_slides: int = 6  # Number of slides to generate

    # Contact information
    customize_contact_info: bool = True  # Whether to use custom contact info
    your_full_name: str = "J. Doe"  # Presenter name
    your_contact_info: str = "j.doe@anycompany.com"  # Contact email
    your_title: str = "Chief Presentation Officer"  # Job title
    your_company: str = "AnyCompany"  # Company name

    # Slide options
    create_agenda_checkbox: bool = True  # Whether to add agenda slide
    create_thankyou_checkbox: bool = True  # Whether to add thank you slide
    slides_format_json: dict = None  # Slide format JSON

    # Background/image generation
    selected_generate_bkg: bool = True  # Whether to generate background
    generate_bkg: bool = True  # Internal flag for background generation
    bkg_prompt: str = (
        "digital presentation wallpaper, dark blue tone, uniform color, corner gradient towards orange"  # Background image prompt
    )
    gen_bkg_price_cents: int = 1  # Price for background generation

    # Image generation settings
    selected_generate_images: str = "Low resolution"  # Image generation quality setting
    generate_images: bool = True  # Whether to generate images
    high_res_images: bool = False  # Whether to use high resolution
    gen_images_price_cents: float = 0.8  # Price per generated image
    generate_thumbnails: bool = False  # Whether to generate slide thumbnails

    # Generation tracking
    n_input_tokens: int = 0  # Number of input tokens used
    n_output_tokens: int = 0  # Number of output tokens used
    n_gen_images: int = 0  # Number of images generated
    content_allowed: bool = False  # Whether content passed moderation
    valid_generation: bool = False  # Whether generation was valid

    # Debug
    debug_mode: bool = False

    # Output path
    output_path: str = None  # Output file path
    output_dir_images: str = None

    def __post_init__(self):
        if self.selected_llm == "Haiku":
            self.chosen_llm = "anthropic.claude-3-haiku-20240307-v1:0"
            self.llm_input_token_price = 0.00025 / 1e3
            self.llm_output_token_price = 0.00125 / 1e3

        if self.selected_generate_bkg is False:
            self.generate_bkg = False
            self.gen_bkg_price_cents = 0

        if self.selected_generate_images == "Do not generate":
            self.generate_images = False
            self.high_res_images = False
            self.gen_images_price_cents = 0.0
        elif self.selected_generate_images == "High resolution":
            self.high_res_images = True
            self.gen_images_price_cents = 1.0

        self.slides_format_json = template_aws1(high_res_images=self.high_res_images)
        self.output_dir_images = self.output_path.replace(".pptx", "/") if self.output_path else None
