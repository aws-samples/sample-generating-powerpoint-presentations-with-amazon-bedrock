
using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;

namespace AgentProxy.Models
{
    public class PPTXGeneratorInput
    {
        public string tenant_id { get; set; }
        public string kb_id { get; set; }
        public string topic { get; set; }
        public string model { get; set; } = "Sonnet";
        public int slides { get; set; } = 6;
        public string background_prompt { get; set; } = "digital presentation wallpaper, dark blue tone, uniform color, corner gradient towards orange";
        public bool agenda { get; set; } = false;
        public bool thankyou { get; set; } = false;
        public bool ImagesOn { get; set; } = false;
        public bool background { get; set; } = false;
        public bool thumbnails { get; set; } = false;
        public bool debug_mode { get; set; } = false;
        public string images { get; set; } = "Low resolution";
        public string name { get; set; } = "J. Doe";
        public string title { get; set; } = "Chief Presentation Officer";
        public string company { get; set; } = "AnyCompany";
        public string email { get; set; } = "XXX@xxx.xxx";

        internal static T GetParameter<T>(ILambdaLogger logger, JsonArray parameters, string name, T defaultValue)
        {
            var param = parameters!.FirstOrDefault(x => x!["name"]!.GetValue<string>() == name);
            var val = param != null ? param["value"]!.GetValue<T>() : defaultValue;
            logger.LogInformation("Get Parameter: {name} - {val}", name, val);
            return val ?? defaultValue;
        }

        public static (SessionState, string) Parse(ILambdaLogger logger, JsonObject input) {

            var parameters = input["parameters"] as JsonArray;
            if (parameters == null) {
                return (null, "parameters not found");
            }
            var sessions = input["sessionAttributes"] as JsonObject;

            var ss = new SessionState() {
                tenant_id=sessions?["tenant_id"]?.GetValue<string>() ?? GetParameter<string>(logger, parameters, "tenant_id", ""),
                kb_id=sessions?["kb_id"]?.GetValue<string>() ?? GetParameter<string>(logger, parameters, "kb_id", ""),
                topic=GetParameter<string>(logger, parameters, "topic", ""),
                n_slides=GetParameter<int>(logger, parameters, "slides", 6),
                selected_llm=GetParameter<string>(logger, parameters, "model", "Sonnet"),
                chosen_llm=Environment.GetEnvironmentVariable("MODEL_ID"),
                create_agenda_checkbox=GetParameter<bool>(logger, parameters, "agenda", false),
                create_thankyou_checkbox=GetParameter<bool>(logger, parameters, "thankyou", false),
                selected_generate_images=GetParameter<string>(logger, parameters, "images", "Low resolution"),
                selected_generate_bkg=GetParameter<bool>(logger, parameters, "background", true),
                bkg_prompt=GetParameter<string>(logger, parameters, "background_prompt", "A pig wearing a baseball cap with text 'genai', summer scenery, blue sky, morning light. Canon R5, 600mm f/4, wildlife photography."),
                generate_thumbnails=GetParameter<bool>(logger, parameters, "thumbnails", false),
                your_full_name=GetParameter<string>(logger, parameters, "name", "J. Doe"),
                your_title=GetParameter<string>(logger, parameters, "title", "Chief Presentation Officer"),
                your_company=GetParameter<string>(logger, parameters, "company", "AnyCompany"),
                your_contact_info=GetParameter<string>(logger, parameters, "email", "XXX@xxx.xxx"),
                debug_mode=GetParameter<bool>(logger, parameters, "debug_mode", true)
            };
            
            if (ss.tenant_id == null || ss.kb_id == null || ss.topic == null ) {
				return (null, "tenant id, kb_id and topic are required");

			}

            logger.LogDebug("state: {0}", JsonSerializer.Serialize(ss));

            return (ss, null);
        }
    }
}