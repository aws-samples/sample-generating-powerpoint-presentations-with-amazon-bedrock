
using AgentProxy.Models;

namespace AgentProxy.Models
{
    public class SessionState
    {

        public string tenant_id { get; set; }
        public string kb_id { get; set; }

        // Language model settings
        public string selected_llm { get; set; } = "Sonnet";  // Selected language model name
        public string chosen_llm { get; set; } = "anthropic.claude-3-sonnet-20240229-v1:0";  // Model ID/version
        public float llm_input_token_price { get; set; } = 0.003F;// / 1e3  // Price per input token
        public float llm_output_token_price { get; set; } = 0.015F;// / 1e3  // Price per output token

        // Presentation topic/content
        public string topic { get; set; } = "Benefits of cloud computing with Amazon Web Services";  // Main topic
        public string topic_from_text { get; set; } = "Benefits of cloud computing with Amazon Web Services";  // Copy of topic
        public int n_slides { get; set; } = 6;  // Number of slides to generate

        // Contact information
        public bool customize_contact_info { get; set; } = true;  // Whether to use custom contact info
        public string your_full_name { get; set; } = "J. Doe";  // Presenter name
        public string your_contact_info { get; set; } = "j.doe@anycompany.com";  // Contact email
        public string your_title { get; set; } = "Chief Presentation Officer";  // Job title
        public string your_company { get; set; } = "AnyCompany";  // Company name

        // Slide options
        public bool create_agenda_checkbox { get; set; } = true;  // Whether to add agenda slide
        public bool create_thankyou_checkbox { get; set; } = true;  // Whether to add thank you slide
        public string slides_format_json { get; set; }  // Slide format JSON

        // Background/image generation
        public bool selected_generate_bkg { get; set; } = true;  // Whether to generate background
        public bool generate_bkg { get; set; } = true;  // Internal flag for background generation
        public string bkg_prompt { get; set; } = "digital presentation wallpaper, dark blue tone, uniform color, corner gradient towards orange";  // Background image prompt
        public int gen_bkg_price_cents { get; set; } = 1;  // Price for background generation

        // Image generation settings
        public string selected_generate_images { get; set; } = "Low resolution";  // Image generation quality setting
        public bool generate_images { get; set; } = true;  // Whether to generate images
        public bool high_res_images { get; set; } = false;  // Whether to use high resolution
        public float gen_images_price_cents { get; set; } = 0.8F;  // Price per generated image
        public bool generate_thumbnails { get; set; } = false;  // Whether to generate slide thumbnails

        // Generation tracking
        public int n_input_tokens { get; set; } = 0;  // Number of input tokens used
        public int n_output_tokens { get; set; } = 0;  // Number of output tokens used
        public int n_gen_images { get; set; } = 0;  // Number of images generated
        public bool content_allowed { get; set; } = false; // Whether content passed moderation
        public bool valid_generation { get; set; } = false; // Whether generation was valid

        // Debug
        public bool debug_mode { get; set; } = false;

        // Output path
        public string output_path { get; set; }  // Output file path
        public string output_dir_images { get; set; }

        public SessionState()
        {
            if (selected_llm == "Haiku")
            {
                chosen_llm = "anthropic.claude-3-haiku-20240307-v1:0";
                llm_input_token_price = 0.00025F;
                llm_output_token_price = 0.00125F;


                if (!selected_generate_bkg)
                {
                    generate_bkg = false;
                    gen_bkg_price_cents = 0;
                }
                if (selected_generate_images == "Do not generate")
                {
                    generate_images = false;
                    high_res_images = false;
                    gen_images_price_cents = 0.0F;
                }
                else if (selected_generate_images == "High resolution")
                {
                    high_res_images = true;
                    gen_images_price_cents = 1.0F;
                }

                slides_format_json = template(high_res_images);
                output_dir_images = !string.IsNullOrEmpty(output_path) ? output_path.Replace(".pptx", "/") : null;
            }
        }

        internal static string template(bool high_res_images = false)
        {
            var image_size_long = 576;
            var image_size_short = 384;
            if (high_res_images)
            {
                image_size_long = 1152;
                image_size_short = 768;
            }

            return
$@"
{{
    'Title page': {{
        'layout_slide': 0,
        'title_placeholder': 0,
        'subtitle_placeholder': 1,
        'full_name_placeholder': 11,
        'job_title_placeholder': 12,
        'text_placeholder': 2,
        'image_placeholder': null
    }},
    'Agenda': {{
        'layout_slide': 1,
        'title_placeholder': 0,
        'agenda_title': 'Today's agenda',
        'text_placeholder': 10,
        'image_placeholder': null
    }},
    'Slide with bullet points': {{
        'layout_slide': 2,
        'title_placeholder': 0,
        'subtitle_placeholder': 10,
        'text_placeholder': 1
    }},
    'Slide with image and text': {{
        'layout_slide': 3,
        'title_placeholder': 0,
        'text_placeholder': 1,
        'image_text_field': 'text',
        'image_placeholder': 10,
        'image_height': {image_size_long},
        'image_width': {image_size_short}
    }},
    'Slide with image only': {{
        'layout_slide': 4,
        'image_placeholder': 10,
        'image_text_field': 'title',
        'image_height': {image_size_short},
        'image_width': {image_size_long}
    }},
    'Slide with 4 takeaways': {{
        'layout_slide': 5,
        'title_placeholder': 0,
        'text1_placeholder': 17,
        'text2_placeholder': 18,
        'text3_placeholder': 19,
        'text4_placeholder': 20,
    }},
    'Thank you': {{
        'layout_slide': 6,
        'title_placeholder': 0,
        'full_name_placeholder': 10,
        'contact_info_placeholder': 11
    }}
}}
";
        }
    }
}
