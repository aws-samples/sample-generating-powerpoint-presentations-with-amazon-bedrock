using AgentProxy.Models;
using Amazon.Lambda.Core;
using System.Text.Json.Nodes;
using System.Text.Json;
using Amazon.BedrockAgentRuntime;
using Amazon.BedrockAgentRuntime.Model;

namespace AgentProxy.Util {
    public static class LambdaHelper {

        public static async Task<AgentProxy.Models.SessionState> check_content_moderation(ILambdaLogger logger, AgentProxy.Models.SessionState ss) {
            //"""Check content moderation for the topic."""
            var input = PromptHelper.moderation_prompt(ss.topic);
            (JsonNode? moderate_request_response, JsonNode? usage) result = await BedrockHelper.invoke_llm_text(logger, input);
            ss.n_input_tokens = ss.n_input_tokens + result!.usage["input_tokens"]!.GetValue<int>();
            ss.n_output_tokens = ss.n_output_tokens + result!.usage["output_tokens"]!.GetValue<int>();

            try {
                var contentModeration = JsonSerializer.Deserialize<ContentModerationResult>(result.moderate_request_response![0]!["text"]!.GetValue<string>());
                ss.content_allowed = contentModeration?.ContentAllowed ?? false;
            }
            catch (Exception e) {
                logger.LogError(e,"Content not allowed, SKIPPING");
                ss.content_allowed = false;
            }

            logger.LogInformation("Content allowed: {0}", ss.content_allowed);

            if (!ss.content_allowed) {
                throw new Exception("Content not allowed based on moderation check. Please try again.");
            }

            return ss;
        }
    
        public static async Task<(string, bool, RetrieveAndGenerateResponse)> generate_initial_text(ILambdaLogger logger, AgentProxy.Models.SessionState ss) {
            //"""Generate initial text content for the presentation."""
            var bedrock_response = await BedrockHelper.generate_text_kb(logger, ss);
            var initial_summary = bedrock_response.Output.Text;
            logger.LogInformation("initial_summary: {0}", initial_summary);

            if (bedrock_response.GuardrailAction == GuardrailAction.INTERVENED) {
                logger.LogWarning("There was a problem with the answer from Claude, try again");
                return (initial_summary, false, bedrock_response);
            }

            var initial_prompt = PromptHelper.create_initial_prompt(ss.n_slides, initial_summary);
            (JsonNode? text_gen_result, JsonNode? usage) result = await BedrockHelper.generate_text(logger, initial_prompt, ss.n_slides, ss.chosen_llm);
            ss.n_input_tokens += result!.usage["input_tokens"]!.GetValue<int>();
            ss.n_output_tokens += result!.usage["output_tokens"]!.GetValue<int>() ;

            var list = result.text_gen_result as JsonArray;

            if (result.text_gen_result == null || (list != null && list.Count != 1) ) {
                logger.LogWarning("There was a problem with the answer from Claude, try again");
                return ("", false, bedrock_response);
            }

            return (list![0]!["text"]!.GetValue<string>(), true, bedrock_response);

        }


        public static async Task<(Presentation?, string?, RetrieveAndGenerateResponse?)> generate_presentation_content(ILambdaLogger logger, AgentProxy.Models.SessionState ss) {

            //"""Main presentation generation loop."""
            logger.LogInformation("Generating presentation.. The application will try to self-heal in case of errors");

            var max_generation_attempts = 2;
            var generation_attempts = 0;
            Presentation? presentation_slides = null;
            RetrieveAndGenerateResponse? rag_response = null;
            var success = false;

            // Adjust number of slides based on agenda and thank you slides
            var actual_slides_needed = ss.n_slides;
            if (!ss.create_agenda_checkbox) {
                actual_slides_needed -= 1;
                logger.LogInformation("Adjusting for agenda slide. Slides needed: {0}", actual_slides_needed);
            }
            if (!ss.create_thankyou_checkbox) {
                actual_slides_needed -= 1;
                logger.LogInformation("Adjusting for thank you slide. Slides needed: {0}", actual_slides_needed);
            }

            while(generation_attempts < max_generation_attempts) {
                // Generate initial content with adjusted slide count
                ss.n_slides = actual_slides_needed; // Temporarily adjust n_slides
                (string raw_generated_json, bool gen_success, RetrieveAndGenerateResponse rag_response) result = await generate_initial_text(logger, ss);
                ss.n_slides = actual_slides_needed + (ss.create_agenda_checkbox ? 1 : 0) + (ss.create_thankyou_checkbox ? 1 : 0); // Restore original n_slides
                rag_response = result.rag_response;

                if (!result.gen_success) {
                    if (result.rag_response.GuardrailAction == GuardrailAction.INTERVENED) {
                        return (null, $"Failed to generate valid presentation content after {max_generation_attempts} attempts", result.rag_response);
                    }
                    generation_attempts += 1;
                    continue;
                }

                // Fix and validate JSON
                (string raw_generated_json, bool json_valid) result2 = await JsonHelper.fix_json_content(logger, result.raw_generated_json, ss);
                if (!result2.json_valid) {
                    generation_attempts += 1;
                    continue;
                }

                // Parse JSON structure
                (JsonNode? validated_json_content, bool struct_valid) result3 = JsonHelper.validate_json_structure(logger, result2.raw_generated_json);
                if (!result3.struct_valid) {
                    ss.valid_generation = false;
                    generation_attempts += 1;
                    continue;
                }

                // Check slides consistency against adjusted slide count
                presentation_slides = JsonSerializer.Deserialize<Presentation>(result3!.validated_json_content!.ToJsonString());
                bool is_consistent = JsonHelper.check_text_generation_consistency(logger, presentation_slides, actual_slides_needed);

                if (is_consistent) {
                    ss.valid_generation = true;
                    success = true;
                    break;
                }

                generation_attempts += 1;

            }

            if (!success) {
                return (null, $"Failed to generate valid presentation content after {max_generation_attempts} attempts", rag_response);
            }
            var numberOfContents = presentation_slides !=  null ? presentation_slides.Slides.Count() : 0;
            logger.LogInformation("Successfully generated {0} slides (excluding agenda/thank you slides)", numberOfContents);

            return (presentation_slides, null, rag_response);
        }
    }

}