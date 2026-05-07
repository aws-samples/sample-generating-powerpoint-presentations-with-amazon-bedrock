
using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;
using AgentProxy.Models;
using AgentProxy.Util;
using Json.More;
using System.Runtime.InteropServices.JavaScript;

// Assembly attribute to enable the Lambda function's JSON input to be converted into a .NET class.
// [assembly: LambdaSerializer(typeof(Amazon.Lambda.Serialization.SystemTextJson.DefaultLambdaJsonSerializer))]

namespace AgentProxy 
{
    public class SummaryGenerator
    {
        
        /// <summary>
        /// A simple function that takes a string and does a ToUpper
        /// </summary>
        /// <param name="input">The event for the Lambda function handler to process.</param>
        /// <param name="context">The ILambdaContext that provides methods for logging and describing the Lambda environment.</param>
        /// <returns></returns>
        public async Task<dynamic> FunctionHandler(JsonObject input, ILambdaContext context)
        {
            try
            {
                var config = SummaryGeneratorInput.Parse(context.Logger, input);
                var result = await BedrockHelper.generate_text_kb(context.Logger, config.prompt, config.kb_id, config.model_id, null, "kb-summary-guardrail", 5);
                var citations = result.Citations.Select(c => new CitationChunk { Text = c.GeneratedResponsePart.TextResponsePart.Text, Sources = Enumerable.Range(0, c.RetrievedReferences.Count).ToDictionary(i => i, i => c.RetrievedReferences[i].Metadata["x-amz-bedrock-kb-source-uri"].AsString())});
                var citationJson = JsonObject.Parse(JsonSerializer.Serialize(citations));
                var response = ResponseFormater.format_bedrock_response(context.Logger, input, result, new JsonObject { ["chunks"] = citationJson}, null);
                return response;
            }
            catch (Exception ex)
            {
                context.Logger.LogError(ex, ex.Message);
                return ResponseFormater.format_bedrock_response(context.Logger, input, null, null, ex.Message);
            }
        }
    }

}