using System.Text.Json.Serialization;

namespace AgentProxy.Models {
    public class ContentModerationResult {
        [JsonPropertyName("content_allowed")]
        public bool ContentAllowed { get; set; }
    }

}