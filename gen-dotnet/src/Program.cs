

using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using AgentProxy.Util;

namespace AgentProxy;

internal class Program
{
    // static async Task Main(string[] args)
    // {
    //     Console.WriteLine("Test OpenXml");
    //     var slides = JsonSerializer.Deserialize<JsonObject>(File.ReadAllText("presentation_slides.json"))["slides"] as JsonArray;

    //     await PowerPointHelper.GeneratePowerPoint(
    //         null,
    //         new PowerPointContext {
    //             Name = "Tester",
    //             Title = "Captain",
    //             Company = "Starfleet",
    //             Prompt = "This is the user prompt used to generate the content for this powerpoint presentation",
    //             ContactInfo = "Tester's contact info",
    //             // TemplatePath = "sample.pptx",
    //         }
    //         , slides);
    // }
}