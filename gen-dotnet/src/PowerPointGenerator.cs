using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;
using AgentProxy.Models;
using AgentProxy.Util;
using Amazon.BedrockAgentRuntime.Model;

// Assembly attribute to enable the Lambda function's JSON input to be converted into a .NET class.
// [assembly: LambdaSerializer(typeof(Amazon.Lambda.Serialization.SystemTextJson.DefaultLambdaJsonSerializer))]

namespace AgentProxy
{
    public class PowerPointGenerator
    {
        
        /// <summary>
        /// A simple function that takes a string and does a ToUpper
        /// </summary>
        /// <param name="input">The event for the Lambda function handler to process.</param>
        /// <param name="context">The ILambdaContext that provides methods for logging and describing the Lambda environment.</param>
        /// <returns></returns>
        public async Task<dynamic> FunctionHandler(JsonObject input, ILambdaContext context)
        {
            var logger = context.Logger;
            try
            {
                logger.LogDebug("Input: {0}", input);
                // Parse input
                logger.LogDebug("Parsing input");
                (AgentProxy.Models.SessionState state, string error) result = PPTXGeneratorInput.Parse(logger, input);
                if (result.error != null)
                {
                    context.Logger.LogDebug(result.error);
                    return ResponseFormater.format_bedrock_response(logger, input, null, null, result.error);
                }

                var state = result.state;//await LambdaHelper.check_content_moderation(logger, result.state);
                context.Logger.LogInformation("STARTING PRESENTATION GENERATION!");
                // Use /tmp for Lambda temporary storage
                var start_time = DateTime.UtcNow;

                (Presentation? presentation_slides, string? error_response, RetrieveAndGenerateResponse? rag_response) result1 = await LambdaHelper.generate_presentation_content(logger, state);
                if (result1.error_response != null) {
                    return ResponseFormater.format_bedrock_response(logger, input, result1.rag_response, null, result1.error_response);
                }

                if (result1!.presentation_slides == null)
                {
                    return ResponseFormater.format_bedrock_response(logger, input, result1.rag_response, null, "No slides were generated.");
                }
                
                var pptx_context = new PowerPointContext {
                    Title = state.your_title,
                    Company = state.your_company,
                    Name = state.your_full_name,
                    ContactInfo = state.your_contact_info,
                    ImageHighResolution = state.high_res_images
                };
                if (state.selected_generate_bkg) {
                    state.n_gen_images +=1;
                    context.Logger.LogDebug("Image path: {0}", pptx_context.FilePathForBackgroundImage);
                }

                if (state.generate_thumbnails) {
                    throw new NotImplementedException("Thumbnail generation not implemented yet");
                }
                
                var slides = JsonSerializer.Deserialize<JsonObject>(JsonSerializer.Serialize(result1.presentation_slides))["slides"] as JsonArray;
                var pptx_file_path = await PowerPointHelper.GeneratePowerPoint(context.Logger, pptx_context, slides);
                var presignedUrl = await S3Helper.UploadAndReturnPresignedUrl(context.Logger, pptx_file_path, state.tenant_id);

                // Fix individual slide JSONs
                // json_content_list = [fix_slide_json(slide_dict, ss) for slide_dict in json_content_list]

                var duration = DateTime.UtcNow - start_time;
                var input_token_cost_cent = state.n_input_tokens * state.llm_input_token_price * 100;
                var output_token_cost_cent = state.n_output_tokens * state.llm_output_token_price * 100;
                var image_cost_cent = state.n_gen_images * state.gen_images_price_cents;
                var total_cost_cent = input_token_cost_cent + output_token_cost_cent + image_cost_cent;


                logger.LogInformation("PRESENTATION GENERATION COMPLETED!");
                logger.LogDebug("TOTAL NUMBER INPUT TOKEN:      {0} (¢ {1})", state.n_input_tokens, input_token_cost_cent);
                logger.LogDebug("TOTAL NUMBER OUTPUT TOKENS:    {0} (¢ {1})", state.n_output_tokens, output_token_cost_cent);
                logger.LogDebug("TOTAL NUMBER IMAGES GENERATED: {0} (¢ {1})", state.n_gen_images, image_cost_cent);
                logger.LogDebug("TOTAL COST IN CENTS OF $:      ¢ {0}", total_cost_cent);
                logger.LogDebug("TOTAL PPTX GENERATION TIME:    {0} seconds", duration);

                var response_body = new JsonObject { ["filename"]= pptx_file_path, ["url"] = presignedUrl};

                return ResponseFormater.format_bedrock_response(logger, input, result1.rag_response, response_body, null);

            }
            catch (Exception ex)
            {
                context.Logger.LogError(ex, ex.Message);
                return ResponseFormater.format_bedrock_response(logger, input, null, null, ex.Message);
            }
        }
    }
}
