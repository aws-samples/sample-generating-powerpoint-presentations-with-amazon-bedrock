using AgentProxy.Models;
using System.Text.Json;
using System.Text.Json.Nodes;
using Json.Schema;
using Amazon.Lambda.Core;
using System.Text.RegularExpressions;

namespace AgentProxy.Util 
{
    public static class JsonHelper {

        private static string clean_json_string(string json_str) {
            return json_str.Trim('\'').TrimStart('\'', '`').TrimStart(new char[] {'j','s', 'o', 'n'}).TrimEnd('`').Trim();//.TrimStart('\'',) //"```json", "").Replace("```", "").Trim();
        }

        private static string manual_json_repair(string json_str) {
            // Remove any trailing commas before closing braces or brackets
            var tmp = Regex.Replace(json_str, @"(?<comma>,)\s*[\}\]]", m => m.Groups["comma"].Success ? "" : m.Value);
            // json_str = Regex.Replace(json_str, ", "(\\s*[}\\]])", "$1");
            // // Add missing quotes around keys
            tmp = Regex.Replace(tmp, @"([{, ])(?<key>\w+)(:)", m => m.Groups["key"].Success ? $"\"{m.Groups["key"].Value}\"" : m.Value);
            // json_str = Regex.Replace(json_str, "([{, ])(\\w+)(:)", "$1\"$2\"$3");
            // // Replace single quotes with double quotes
            tmp = Regex.Replace(tmp, @"[\{\,\[]\s*(?<singlequote>')", m => m.Groups["singlequote"].Success ? "\"" : m.Value);
            tmp = Regex.Replace(tmp, @"(?<singlequote>')[\:\,\s*\}]", m => m.Groups["singlequote"].Success ? "\"" : m.Value);
            return tmp;
        }
        
        private static (string, bool) try_parse_json(ILambdaLogger logger, string json_str) {
            try {
                logger.LogDebug("Json to parse: {0}", json_str);
                var parsed_json = JsonNode.Parse(json_str);
                return (parsed_json.ToJsonString(), true);
            } catch (Exception e) {
                logger.LogError(e, "JSON Parse Error: {0}", e.Message);
                return (json_str, false);
            }
        }


        public static async Task<(string, bool)> fix_json_content(ILambdaLogger logger, string raw_json, SessionState ss) {
            logger.LogInformation("Attempting to validate and fix JSON content...");
            logger.LogDebug("Raw JSON: {0}", raw_json);

            // First attempt: Try to parse the cleaned JSON
            var cleaned_json = clean_json_string(raw_json);
            logger.LogDebug("Cleaned JSON: {0}", cleaned_json);
            (string formatted_json, bool is_valid) result = try_parse_json(logger, cleaned_json);

            if (result.is_valid) {
                logger.LogInformation("JSON successfully validated on first attempt!");
                return (result.formatted_json, true);
            }

            // Second attempt: Try manual repair
            var repaired_json = manual_json_repair(cleaned_json);
            (string formatted_json, bool is_valid) result2 = try_parse_json(logger, repaired_json);

            if (result2.is_valid) {
                logger.LogInformation("JSON successfully repaired manually!");
                return (result2.formatted_json, true);
            }

            // If manual repair fails, attempt fixes using the model
            var max_fix_attempts = 2;
            foreach (var i in Enumerable.Range(0, max_fix_attempts)) {
                logger.LogInformation("Attempt {0} to fix JSON using model...", (i + 1));

                try {
                    // Generate fixed JSON using the model
                    (dynamic raw_gen_result_fixed, dynamic usage) result3 = await BedrockHelper.generate_text(
                        logger,
                        PromptHelper.fix_initial_json_prompt(raw_json),
                        ss.n_slides,
                        ss.chosen_llm
                    );

                    // Update token usage
                    ss.n_input_tokens += result3.usage["input_tokens"];
                    ss.n_output_tokens += result3.usage["output_tokens"];

                    // Get the fixed JSON content and clean it
                    var fixed_json = result3.raw_gen_result_fixed[0]["text"];
                    var cleaned_fixed_json = clean_json_string(fixed_json);

                    // Try to repair and parse the fixed JSON
                    repaired_json = manual_json_repair(cleaned_fixed_json);
                    (string formatted_json, bool is_valid) result4 = try_parse_json(logger, repaired_json);

                    if (result4.is_valid) {
                        logger.LogInformation("JSON successfully fixed!");
                        return (result4.formatted_json, true);
                    }

                    logger.LogWarning("Generated JSON still contains errors, will attempt another fix...");
                    // Print first 200 characters of problematic JSON for debugging
                    logger.LogDebug("Problematic JSON preview: {}...", cleaned_fixed_json);
                }
                catch (Exception e) {
                    logger.LogError(e, "JSON Parse Error: {}", e.Message);
                }
            }
            logger.LogError("\nFailed to fix JSON after all attempts");
            return (raw_json, false);
        }

        public static (JsonNode, bool) validate_json_structure(ILambdaLogger logger, string raw_json) {
            try {
                var result = JsonNode.Parse(raw_json);  
                return (result, true);
            } catch (Exception e) {
                logger.LogError(e, "Failed to validate JSON structure: {}", e.Message);
                try {
                    var result = JsonNode.Parse(raw_json+ "}]}");
                    return (result, true);
                } catch (Exception e2) {
                    logger.LogError(e2, "Failed to validate JSON structure: {}", e2.Message);
                }
                return (null, false);
            }
    	
        }
    
        public static bool validate_slide_json(ILambdaLogger logger, JsonNode slide_json) {
            try {
                var format = slide_json["slideFormat"]!.GetValue<string>();
                bool isImageSlide = format == "Slide with image only" || format == "Slide with image and text";
                var addTextAsRequired = isImageSlide ? ",\"text\"" : "";
                var expected_schema = $@"""{{
                    ""type"": ""object"",
                    ""properties"": {{
                        ""slide_n"": {{""type"": ""number""}},
                        ""title"": {{""type"": ""string""}},
                        ""subtitle"": {{""type"": ""string""}},
                        ""text"": {{""type"": ""string""}},
                        ""speaker_notes"": {{""type"": ""string""}},
                        ""slideFormat"": {{""type"": ""string""}},
                    }},
                    ""required"": [
                        ""slide_n"",
                        ""title"",
                        ""subtitle"",
                        ""speaker_notes"",
                        ""slideFormat""{addTextAsRequired}
                    ]
                }}""";

                if (slide_json["text"] == null) {
                    slide_json["text"] = "";
                }

                var schema = JsonSchema.FromText(expected_schema);
                var jsonDoc = JsonDocument.Parse(slide_json.ToJsonString());
                var result = schema.Evaluate(jsonDoc.RootElement);

                return result.IsValid;
            } catch (Exception e) {
                logger.LogError(e, "Failed to validate JSON structure: {}", e.Message);
                return false;
            }
        }

        public static async Task<JsonNode> fix_slide_json(ILambdaLogger logger, JsonNode slide_json, SessionState ss) {
            logger.LogInformation("Attempting to validate and fix JSON content...");

            try {
                logger.LogInformation("Processing slide: {}", slide_json["slide_n"]);
                logger.LogDebug("Original slide data: {}", slide_json.ToJsonString());

                var isValidJson = validate_slide_json(logger, slide_json);

                if (isValidJson) {
                    logger.LogInformation("JSON is valid!");
                    return slide_json;                
                }

                var max_attempts = 3;
                foreach (var i in Enumerable.Range(0, max_attempts)) {
                    logger.LogInformation("Attempt {} to fix JSON...", (i + 1));

                    try {
                        // Generate fixed JSON using the model
                        (JsonNode? raw_gen_result_fixed, JsonNode? usage) result = await BedrockHelper.generate_text(
                            logger,
                            PromptHelper.fix_slide_json_prompt(slide_json.ToJsonString()),
                            ss.n_slides,
                            ss.chosen_llm
                        );

                        // Update token usage
                        ss.n_input_tokens += result!.usage["input_tokens"]!.GetValue<int>();
                        ss.n_output_tokens += result!.usage["output_tokens"]!.GetValue<int>();

                        // Get the fixed JSON content
                        var list = result!.raw_gen_result_fixed as JsonArray;
                        var fixed_json = list![0]!["text"]!.GetValue<string>();

                        // Try to parse and validate the fixed JSON
                        var is_valid = validate_slide_json(logger, fixed_json);

                        if (is_valid) {
                            logger.LogInformation("JSON slide fix successful on attempt {0}", i + 1);
                            return JsonNode.Parse(fixed_json);
                        }

                        logger.LogWarning("Generated JSON still contains errors, will attempt another fix...");
                        // Print first 200 characters of problematic JSON for debugging
                        logger.LogDebug("Problematic JSON preview: {}...", fixed_json.Substring(0, 200));
                    }
                    catch (Exception e) {
                        logger.LogError(e, $"Error parsing fixed JSON on attempt {i}");
                        if (i == max_attempts - 1) {
                            // On last attempt, try to salvage the original with defaults
                            logger.LogInformation("Attempting to salvage original slide with defaults");
                            var salvaged_json = ensure_required_fields(logger, slide_json);
                            if (validate_slide_json(logger, salvaged_json)) {
                                logger.LogInformation("Salvaged original slide with defaults");
                                return salvaged_json;
                            } 
                        }
                    }
                }
                logger.LogWarning("Failed to fix slide after {0} attempts", max_attempts);
                return slide_json;

            }
            catch (Exception e) {
                logger.LogError(e, "Unexpected error in fix_slide_json: {0}", e.Message);
                return ensure_required_fields(logger, slide_json);
            }
        }

        public static JsonNode ensure_required_fields(ILambdaLogger logger, JsonNode slide_json) {
            //"""Ensure all required fields exist with at least empty strings."""
            try {
                var required_fields = new List<string>() {"title", "subtitle", "text", "speaker_notes", "slideFormat"};
                var slide_format = slide_json["slideFormat"]!.GetValue<string>();
                
                // Don't add text field for image-only slides
                if (slide_format == "Slide with image only" || slide_format == "Slide with image and text") {
                    required_fields.Remove("text");
                }

                foreach (var field in required_fields) {
                    var val = slide_json[field];
                    if (val == null) {
                        slide_json[field] = "";
                        logger.LogInformation("Added missing field '{0}' to slide", field);
                    }
                }
                
                // Ensure slide_n exists and is a number
                var slide_n_field = slide_json["slide_n"];
                if (slide_n_field == null)
                    slide_json["slide_n"] = 0;
                    logger.LogInformation("Added default slide_n");
                    
                return slide_json;
            }
            catch (Exception e) {
                logger.LogError(e, $"Error in ensure_required_fields: {e.Message}");
                return slide_json;            
            }

        }

        public static bool check_text_generation_consistency(ILambdaLogger logger, Presentation? presentation, int N_SLIDES) {
            var generated_n_slides = presentation != null ? presentation.Slides.Count() : 0;
            var is_consistent = generated_n_slides == N_SLIDES;
            if (!is_consistent) {
                logger.LogWarning("Inconsistent number of slides! Requested: {0}, Generated: {1}", N_SLIDES, generated_n_slides);
            }
            return is_consistent;
        }
    }
}
