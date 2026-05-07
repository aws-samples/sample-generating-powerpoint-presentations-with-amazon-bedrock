

using System.Text.Json;
using System.Text.Json.Nodes;
using AgentProxy.Models;
using Amazon.BedrockAgentRuntime.Model;
using Amazon.BedrockRuntime;
using Amazon.Lambda.Core;

namespace AgentProxy {
    public static class ResponseFormater {

        public static dynamic format_bedrock_response(ILambdaLogger logger, JsonObject lambdaEvent, RetrieveAndGenerateResponse? rag_response, JsonObject? final, string? error) {

            var response = new JsonObject { ["responseBody"]=new JsonObject{ ["TEXT"]= new JsonObject { ["body"]= "" }}};
            if (rag_response?.GuardrailAction == GuardrailAction.GUARDRAIL_INTERVENED) {
                response["responseState"] = "PEPROMPT";
                response["responseBody"]["TEXT"]["body"] = "Guardrail intervened! Please rephrase your prompt.";
            } else if (!string.IsNullOrEmpty(error)) {
                response["responseState"] = "FAILURE";
                response["responseBody"]["TEXT"]["body"] = JsonSerializer.Serialize(new { error });
            } else {
                response["responseBody"]["TEXT"]["body"] = JsonSerializer.Serialize(final);
            }

            var functionResponse = new  {
                actionGroup=lambdaEvent["actionGroup"],
                function=lambdaEvent["function"],
                functionResponse=response
            };

            var actionResponse = new {
                messageVersion="1.0",
                response=functionResponse,
                sessionAttributes=lambdaEvent["sessionAttributes"],
                promptSessionAttributes=lambdaEvent["promptSessionAttributes"]
            };
            logger.LogDebug("Bedrock Agent Response: {0}", JsonSerializer.Serialize(actionResponse));
            return actionResponse;
        }

    }
}