namespace AgentProxy
{
    public class PowerPointContext
    {
        public Guid ReferenceId { get; set; } = Guid.NewGuid();
        public string Name { get; set; }
        public string Title { get; set; }
        public string Company { get; set; }
        public string Prompt { get; set; }
        public string ContactInfo { get; set; }
        public bool ImageHighResolution { get; set; } = false;
        public string FilePathForBackgroundImage { get; set; } = "./template/default_bkg.jpg";
        public string TemplatePath { get; set; } = "./template/pptx_base_template.pptx";
        public string MappingJsonPath { get; set; } = "./template/presentation_template_mapping.json";
    }
}