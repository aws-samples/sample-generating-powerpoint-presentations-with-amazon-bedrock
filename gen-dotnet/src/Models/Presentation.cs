using System.Text.Json.Serialization;

namespace AgentProxy.Models {
    public class Presentation {
        [JsonPropertyName("slides")]
        public Slide[] Slides { get; set; } = new Slide[] { };
    }

    public class Slide {
        [JsonPropertyName("slide_n")]
        public int Number { get; set; }
        [JsonPropertyName("title")]
        public string Title { get; set; }
        [JsonPropertyName("subtitle")]
        public string Subtitle { get; set; }
        [JsonPropertyName("text")]
        public string Text { get; set; }
        [JsonPropertyName("speaker_nodes")]
        public string SpeakerNotes { get; set; }
        [JsonPropertyName("slideFormat")]
        public string Format { get; set; }
    }

}