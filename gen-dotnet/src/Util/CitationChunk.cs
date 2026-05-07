
using System.Text.Json.Serialization;

namespace AgentProxy.Models {
    public class CitationChunk {
        [JsonPropertyName("text")]
        public string Text { get; set; }
        [JsonPropertyName("sources")]
        public Dictionary<int, string> Sources { get; set; }

    }
}