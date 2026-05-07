
using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;

namespace AgentProxy.Models
{
    public class SummaryGeneratorInput
    {
        public string prompt { get; set; }
        public string kb_id { get; set; }
        public string model_id  { get; set; }
        private static T GetParameter<T>(ILambdaLogger logger, JsonArray parameters, string name, T defaultValue)
        {
            var param = parameters!.FirstOrDefault(x => x!["name"]!.GetValue<string>() == name);
            var val = param != null ? param["value"]!.GetValue<T>() : defaultValue;
            logger.LogInformation("Get Parameter: {name} - {val}", name, val);
            return val ?? defaultValue;
        }

        public static SummaryGeneratorInput Parse(ILambdaLogger logger, JsonObject input) {
            var config = new SummaryGeneratorInput {
                prompt = GetParameter<string>(logger, input["parameters"]!.AsArray(), "prompt", null),
                kb_id = GetParameter<string>(logger, input["parameters"]!.AsArray(), "kb_id", null),
                model_id = GetParameter<string>(logger, input["parameters"]!.AsArray(), "model_id", "anthropic.claude-3-sonnet-20240229-v1:0")
            };

            return config;
        }
    }
}