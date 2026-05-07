using System.Text.Json.Nodes;
using System.Text.Json;

namespace AgentProxy.Util {
    public static class PromptHelper {
        private static string lambda_root = System.Environment.GetEnvironmentVariable("LAMBDA_TASK_ROOT");

        public static string moderation_prompt(string topic, bool answerForAllowedContent = true, bool answerForDeniedContent = false) {
            var contentAllowed1 = answerForAllowedContent ? "true" : "false";
            var contentAllowed2 = answerForDeniedContent ? "true" : "false";
            return
$@"
A user would like to propose the following topic for a presentation: {topic}.

- Evaluate if the requested topic is potentially offensive and allow or deny the request
- Deny offensive content requests
- Deny unethical content requests
- Deny illegal content requests
- Answer without preamble and in JSON format

Answer for allowed content:
{{""content_allowed"": {contentAllowed1}}}
Answer for denied content:
{{""content_allowed"": {contentAllowed2}}}
";
        }

        public static string create_initial_prompt(int nSlides, string summary) {
            var prompt =
$@"
Here is a summary: {summary}

Your task is to propose a {nSlides} slide presentation on the summary above. 

The presentation should follow these requirements:
- Use only the following slide formats: Title page, Slide with bullet points, Slide with image and text, Slide with image only, Slide with 4 takeaways
- The first slide must be a Title page
- The last slide must be a Slide with 4 takeaways
- Do not use the same content as the example provided

Your response should be in JSON format with the following structure:

{{
""slides"": [
    {{
    ""slide_n"": 1,
    ""title"": ""..."",
    ""subtitle"": ""..."",
    ""text"": ""..."",
    ""speaker_notes"": ""..."",
    ""slideFormat"": ""Title page""
    }},
    {{
    ""slide_n"": 2,
    ""title"": ""..."",
    ""subtitle"": ""..."",
    ""text"": ""..."",
    ""speaker_notes"": ""..."",
    ""slideFormat"": ""...""
    }},
    ...
    {{
    ""slide_n"": n,
    ""title"": ""..."",
    ""subtitle"": ""..."",
    ""text"": ""..."",
    ""speaker_notes"": ""..."",
    ""slideFormat"": ""Slide with 4 takeaways""
    }}
]
}}

Note: For ""Slide with image only"" format, the ""text"" field can be omitted or left empty.

For the Title page slide:
<slide_n>1</slide_n>
<title>Come up with an engaging title for the presentation</title>
<subtitle>Add a subtitle that captures the essence of the topic</subtitle>
<text>Provide a brief overview of what the presentation will cover</text>
<speaker_notes>Introduce yourself and give context for the presentation topic</speaker_notes>
<slideFormat>Title page</slideFormat>

For the intermediate slides (slide 2 to slide {nSlides-1}):
<slide_n>Increment this number for each new slide</slide_n>
<title>Create a title summarizing the main point of this slide</title>
<subtitle>Add a subtitle to complement the title</subtitle>
<text>If using a Slide with bullet points format: *** Include 3-5 bullet points covering key information for this slide ***. Otherwise write 2-3 concise paragraphs with supporting details for the slide topic</text>
<speaker_notes>Add relevant notes to help explain or expand on the slide content</speaker_notes>
<slideFormat>
Choose one of the following formats based on the content:
- Slide with bullet points
- Slide with image and text
- Slide with image only
</slideFormat>

For the final Key Takeaways slide:
<slide_n>{nSlides}</slide_n>
<title>Important Takeaways</title>
<subtitle>Key messages to remember</subtitle>
<text>*** Summarize the 4 most crucial points covered in the presentation ***</text>
<speaker_notes>Remind the audience of the key information you want them to walk away with</speaker_notes>
<slideFormat>Slide with 4 takeaways</slideFormat>

Remember to:
- Use a unique and relevant title, subtitle, text, and speaker notes for each slide
- Vary the slide formats to make the presentation engaging
- Follow the specified JSON structure above
- Do not copy content from the example provided below

Guidelines for text length:
- Title: Maximum 50 characters (shorter titles are more impactful)
- Subtitle: Maximum 75 characters
- Main text: Maximum 300 characters per slide
- Bullet points: Maximum 4-5 points, each 60 characters or less
- Speaker notes: Maximum 200 characters

Very important: Keep titles concise and avoid text overflow by following these length limits.
";
            return prompt + ppt_example_single_shot();
        }

        public static string ppt_example_single_shot() {
            var example = File.ReadAllText(Path.Combine(lambda_root, "config/pptx_example_single_shot.json"));
            return @"

Below is an example of valid JSON schema with 7 slides answering the question: Propose a 7 slides presentation about Generative AI

" + example;
        }

        public static string fix_initial_json_prompt(string rawJson) {
            return
$@"
Fix the broken JSON below:
- Use empty strings instead of Null values
- Escape quotes
- Return a valid JSON format

Very important: Skip the preamble

Here is the broken JSON:
{rawJson}
";
        }

        public static string fix_slide_json_prompt(JsonNode slide_json) {

            var prompt = $@"""
{ppt_example_single_shot()}

Fix and add the missing fields to the broken JSON below, according to the slide examples above. The response should only include a valid JSON format.
Note: For ""Slide with image only"" format, the ""text"" field can be omitted or left empty.

{slide_json.ToJsonString()}
""";
            return prompt;
        }

        public static string create_initial_prompt_bkp(int nSlides, string topic) {
            var prompt = $@"
Propose a {nSlides} slides presentation on the topic: {topic}.

For each slide detail:
- Title
- Subtitle
- Text
- Speaker notes
- Slide format 

Use only the following slide format options: 
- Title page
- Slide with bullet points
- Slide with image and text
- Slide with image only
- Slide with 4 takeaways

Follow these requirements:
- Remove the preamble and answer in JSON format.
- Don't use the same content as the example below
- Update the topic and number of slides as per request above
- Always start with a slide of format ""Title page""
- Always end with a slide of format ""Slide with 4 takeaways""
";
            return prompt + ppt_example_single_shot();
        }

        public static string agenda_prompt(string[] slide_titles) {
            var prompt = $@"
The following list contains slide titles for a slideshow: {JsonSerializer.Serialize(slide_titles)}.

Create 5 bullet points in JSON format summarizing provided the slide titles to fit in the agenda slide: 

Follow these requirements:
- Remove the preamble and answer in JSON format
- Don't use the same content as the example below
- Always end with ""Conclusions""

Below is an example of the JSON schema with 5 example bullet points:
{{
""agenda_points"": ""*** Responsible Development *** Expanded Applications *** Integration with Existing Systems *** Continuous Improvement *** Business success"",
}}";
            // print("MODERATION PROMPT:",prompt)
            return prompt;
        }

        public static string image_prompt(string content) {
            return $@"
Summarize the following content in comma separated abstract concepts, maximum 20 words. The text will be used to generate a representative image with Stable Diffusion. Remove preamble when answering. Content: 
{content}
";
        }
    }
}