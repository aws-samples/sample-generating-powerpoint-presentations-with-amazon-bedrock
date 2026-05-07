using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;
using Amazon.Runtime.Internal.Util;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Drawing;
using DocumentFormat.OpenXml.Office2021.PowerPoint.Comment;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Presentation;
using DocumentFormat.OpenXml.Wordprocessing;
using ColorMapOverride = DocumentFormat.OpenXml.Presentation.ColorMapOverride;
using Picture = DocumentFormat.OpenXml.Presentation.Picture;

namespace AgentProxy.Util
{
    public static class PowerPointHelper {
        private static readonly string OUTPUT_FOLDER = "/tmp";

        private static Picture CreatePicture(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, string imagePath, Int64Value width, Int64Value height)
        {
            var imagePart = slide.SlidePart.AddImagePart(ImagePartType.Jpeg); 
            var picture = new Picture();
            picture.NonVisualPictureProperties = new DocumentFormat.OpenXml.Presentation.NonVisualPictureProperties();
            picture.NonVisualPictureProperties.Append(new DocumentFormat.OpenXml.Presentation.NonVisualDrawingProperties
            {
                Id = (UInt32)slide.CommonSlideData.ShapeTree.ChildElements.Count + 100,
                Name = "background image #1"
            });
            var nonVisualPictureDrawingProperties = new DocumentFormat.OpenXml.Presentation.NonVisualPictureDrawingProperties();
            nonVisualPictureDrawingProperties.Append(new DocumentFormat.OpenXml.Drawing.PictureLocks()
            {
                NoChangeAspect = true,
                NoGrouping = true
            });
            picture.NonVisualPictureProperties.Append(nonVisualPictureDrawingProperties);
            picture.NonVisualPictureProperties.Append(new DocumentFormat.OpenXml.Presentation.ApplicationNonVisualDrawingProperties());
            var blipFill = new DocumentFormat.OpenXml.Presentation.BlipFill();
            var blip1 = new DocumentFormat.OpenXml.Drawing.Blip()
            {
                Embed = slide.SlidePart.GetIdOfPart(imagePart)
            };
            var blipExtensionList1 = new DocumentFormat.OpenXml.Drawing.BlipExtensionList();
            var blipExtension1 = new DocumentFormat.OpenXml.Drawing.BlipExtension()
            {
                Uri = "{28A0092B-C50C-407E-A947-70E740481C1C}"
            };
            var useLocalDpi1 = new DocumentFormat.OpenXml.Office2010.Drawing.UseLocalDpi()
            {
                Val = false
            };
            useLocalDpi1.AddNamespaceDeclaration("a14", "http://schemas.microsoft.com/office/drawing/2010/main");
            blipExtension1.Append(useLocalDpi1);
            blipExtensionList1.Append(blipExtension1);
            blip1.Append(blipExtensionList1);
            var stretch = new DocumentFormat.OpenXml.Drawing.Stretch();
            stretch.Append(new DocumentFormat.OpenXml.Drawing.FillRectangle());
            blipFill.Append(blip1);
            blipFill.Append(stretch);
            picture.Append(blipFill);

            picture.ShapeProperties = new DocumentFormat.OpenXml.Presentation.ShapeProperties();
            picture.ShapeProperties.Transform2D = new DocumentFormat.OpenXml.Drawing.Transform2D();
            picture.ShapeProperties.Transform2D.Append(new DocumentFormat.OpenXml.Drawing.Offset
            {
                X = 0,
                Y = 0,
            });            
            picture.ShapeProperties.Transform2D.Append(new DocumentFormat.OpenXml.Drawing.Extents
            {
                Cx = width,
                Cy = height
            });
            picture.ShapeProperties.Append(new DocumentFormat.OpenXml.Drawing.PresetGeometry
            {
                Preset = DocumentFormat.OpenXml.Drawing.ShapeTypeValues.Rectangle
            });
            using(var imageStream = File.OpenRead(imagePath)) {
                imagePart.FeedData(imageStream);
            }

            return picture;    
        }

        private static void CreateBackgroundImage(ILambdaLogger logger, PowerPointContext context, DocumentFormat.OpenXml.Presentation.Slide slide, DocumentFormat.OpenXml.Presentation.SlideSize size)
        {   
            var picture = CreatePicture(logger, slide, context.FilePathForBackgroundImage, (Int64Value)size.Cx.Value, (Int64Value)size.Cy.Value);

            var tree = slide.CommonSlideData.ShapeTree;
            tree.RemoveAllChildren<DocumentFormat.OpenXml.Drawing.Paragraph>();
            var group = tree.FirstOrDefault(c => c.GetType() == typeof(GroupShapeProperties));
            tree.InsertAfter(picture, group);
        }

        private static void CreateTitlePage(ILambdaLogger logger, PowerPointContext context, DocumentFormat.OpenXml.Presentation.Slide slide, DocumentFormat.OpenXml.Presentation.SlideSize size, JsonObject slide_json, JsonObject mapping) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = slide_json["title"]?.GetValue<string>();
            var subtitle_ph_idx = mapping["subtitle_placeholder"]?.GetValue<int>();
            var subtitle_val = slide_json["subtitle"]?.GetValue<string>();
            var speaker_name_ph_idx = mapping["full_name_placeholder"]?.GetValue<int>();
            var speaker_name_val = context.Name;
            var job_title_ph_idx = mapping["job_title_placeholder"]?.GetValue<int>();
            var speaker_job_val = string.Format("{0}\n{1}", context.Title, context.Company);
            // var text_ph_idx = mapping["text_placeholder"]!.GetValue<int>();
            // var text_val = slide_json["text"]!.GetValue<string>();
            // var speaker_notes_val = string.Format("Input topic/text:\n {0}\n\nGenerated content:\n{1}", speaker.Prompt, mapping["speaker_nodes"]!.GetValue<string>());

            if (title_ph_idx == null) throw new Exception("title placeholder not found");
            if (subtitle_ph_idx == null) throw new Exception("subtitle placeholder not found");
            if (speaker_name_ph_idx == null) throw new Exception("full name placeholder not found");
            if (job_title_ph_idx == null) throw new Exception("job title placeholder not found");

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, subtitle_ph_idx.Value, subtitle_val);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, speaker_name_ph_idx.Value, speaker_name_val);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, job_title_ph_idx.Value, speaker_job_val);
            CreateBackgroundImage(logger, context, slide, size);
            

            //to-do: 
            // 1. replace background image
            // 3. verify what to do with text val

        }

        private static void CreateAgenda(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, JsonObject slide_json, string[] titles, JsonObject mapping) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = mapping["agenda_title"]?.GetValue<string>();
            var text_ph_idx = mapping["text_placeholder"]?.GetValue<int>();
            // var agenda_text_val = string.Join("\n", titles);

            if (title_ph_idx == null) throw new Exception("title placeholder not found");
            if (title_val == null) throw new Exception("agenda title not found");
            if (text_ph_idx == null) throw new Exception("text placeholder not found");

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);
            ReplaceBulletPoints(slide.CommonSlideData.ShapeTree, text_ph_idx.Value, titles);
        }

        private static void CreateBulletPoints(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, JsonObject slide_json, JsonObject mapping) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = slide_json["title"]?.GetValue<string>();
            var subtitle_ph_idx = mapping["subtitle_placeholder"]?.GetValue<int>();
            var subtitle_val = slide_json["subtitle"]?.GetValue<string>();
            var text_ph_idx = mapping["text_placeholder"]?.GetValue<int>();
            var text_val = slide_json["text"]?.GetValue<string>();
            // var speaker_notes_val = string.Format("Input topic/text:\n {0}\n\nGenerated content:\n{1}", speaker.Prompt, mapping["speaker_nodes"]!.GetValue<string>());

            if (title_ph_idx == null) throw new Exception("title placeholder not found");
            if (subtitle_ph_idx == null) throw new Exception("subtitle placeholder not found");
            if (text_ph_idx == null) throw new Exception("text placeholder not found");

            text_val = text_val.Replace("***", "\n").Replace("- ", "");

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, subtitle_ph_idx.Value, subtitle_val);
            ReplaceBulletPoints(slide.CommonSlideData.ShapeTree, text_ph_idx.Value, text_val.Split(("\n")));
        }

        private static async Task CreateImageAndText(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, Guid referenceId, uint slideId, DocumentFormat.OpenXml.Presentation.SlideSize size, JsonObject slide_json, JsonObject mapping, bool highImageResolution = false) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = slide_json["title"]?.GetValue<string>();
            var text_ph_idx = mapping["text_placeholder"]?.GetValue<int>();
            var text_val = slide_json["text"]?.GetValue<string>();
            text_val = text_val!.Replace("***", "\n").Replace("- ", "");
            var image_ph_idx = mapping["image_placeholder"]?.GetValue<int>();
            var main_text_val = slide_json["main_text"]?.GetValue<string>();

            if (title_ph_idx == null) throw new Exception("title placeholder not found");
            if (text_ph_idx == null) throw new Exception("text placeholder not found");
            if (image_ph_idx == null) throw new Exception("image placeholder not found");

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);
            ReplaceBulletPoints(slide.CommonSlideData.ShapeTree, text_ph_idx.Value, [text_val]);
            RemovePlaceholder(logger, slide.CommonSlideData.ShapeTree, 11);
            RemovePlaceholder(logger, slide.CommonSlideData.ShapeTree, 12);
            RemovePlaceholder(logger, slide.CommonSlideData.ShapeTree, 13);

            var filepath = await GenerateImage(logger, main_text_val ?? title_val, string.Format("{0}/image_and_text_slide_{1}.jpg", referenceId, slideId), highImageResolution ? 1152 : 576, highImageResolution ? 768 : 384);
            var width = (Int16Value)(highImageResolution ? 1152 : 576) * 12700;
            var height = (Int64Value)(highImageResolution ? 768 : 384) * 12700;
            AppendNewPictureInPlaceholder(logger, slide, image_ph_idx.Value, filepath, width, height, true);

            //to-do: 
            // 1. insert the generated image into the slide
        }

        private static async Task CreateWithImageOnly(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, Guid referenceId, uint slideId, DocumentFormat.OpenXml.Presentation.SlideSize size, JsonObject slide_json, JsonObject mapping, bool highImageResolution = false) {
            var title_val = slide_json["title"]?.GetValue<string>();
            var main_text_val = slide_json["main_text"]?.GetValue<string>();
            var image_ph_idx = mapping["image_placeholder"]?.GetValue<int>();

            if (image_ph_idx == null) throw new Exception("image placeholder not found");

            var filepath = await GenerateImage(logger, main_text_val ?? title_val, string.Format("{0}/image_only_slide_{1}.jpg", referenceId, slideId), highImageResolution ? 768 : 384, highImageResolution ? 1152 : 576);
            AppendNewPictureInPlaceholder(logger, slide, image_ph_idx.Value, filepath, (Int64Value)size.Cx.Value, (Int64Value)size.Cy.Value);

            //to-do: 
            // 1. insert the generated image into the slide

        }

        private static void CreateTakeaways(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, JsonObject slide_json, JsonObject mapping) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = slide_json["title"]?.GetValue<string>();
            var text_val = slide_json["text"]?.GetValue<string>();

            if (title_ph_idx == null) throw new Exception("title placeholder not found");

            var takeaways = text_val.Split("***").Where(t => !string.IsNullOrEmpty(t)).Select(t => t.Trim()).ToArray();
            var idx = 1;

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);

            foreach(var takeaway in takeaways)
            {
                var ph_idx = mapping[$"text{idx}_placeholder"]?.GetValue<int>();
                if (ph_idx == null) throw new Exception(string.Format("key take away {0} placeholder not found", idx));
                ReplaceParagraphText(slide.CommonSlideData.ShapeTree, ph_idx.Value, takeaway ?? "Not available");
                ++idx;
            }

        }

        private static void CreateThankyou(ILambdaLogger logger, PowerPointContext context, DocumentFormat.OpenXml.Presentation.Slide slide, DocumentFormat.OpenXml.Presentation.SlideSize size, JsonObject slide_json, JsonObject mapping) {
            var title_ph_idx = mapping["title_placeholder"]?.GetValue<int>();
            var title_val = "Thank you!";
            var speaker_name_ph_idx = mapping["full_name_placeholder"]?.GetValue<int>();
            var speaker_name = context.Name;
            var contact_info_ph_idx = mapping["contact_info_placeholder"]?.GetValue<int>();
            var contact_info = context.ContactInfo;

            if (title_ph_idx == null) throw new Exception("title placeholder not found");
            if (speaker_name_ph_idx == null) throw new Exception("full name placeholder not found");
            if (contact_info_ph_idx == null) throw new Exception("contact info placeholder not found");

            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, title_ph_idx.Value, title_val);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, speaker_name_ph_idx.Value, speaker_name);
            ReplaceParagraphText(slide.CommonSlideData.ShapeTree, contact_info_ph_idx.Value, contact_info);
            CreateBackgroundImage(logger, context, slide, size);

            //to-do: 
            // 1. replace background image
        }

        private static async Task<string> GenerateImage(ILambdaLogger logger, string content_for_prompt, string image_path, int image_height, int image_width)
        {
            logger!.LogDebug("content for image prompt: {0}", content_for_prompt);
            var summary_prompt_for_image = PromptHelper.image_prompt(content_for_prompt);
            logger!.LogDebug("summary prompt for image: {0}", summary_prompt_for_image);
            (JsonNode? summary_prompt, JsonNode? usage) response = await BedrockHelper.generate_text(logger, summary_prompt_for_image);
            var image_prompt = string.Format("{0}, abstract", ((JsonArray)response.summary_prompt)?[0]?["text"]?.GetValue<string>());
            logger!.LogDebug("image prompt: {0}", image_prompt);
            var filePath = await BedrockHelper.generate_bedrock_image(logger, image_prompt, image_height, image_width, image_path);
            logger!.LogDebug("image file path: {0}", filePath);
            return filePath;
        }

        private static void ReplaceBulletPoints(ShapeTree tree, int index, string[] bulletpoints)
        {
            var paragraphShape = FindPlaceholderShape(tree, index);
            var listStyle = paragraphShape.Parent.Parent.Parent.Descendants<DocumentFormat.OpenXml.Drawing.ListStyle>().FirstOrDefault();
            if (listStyle == null) throw new Exception("liststyle not found");
            var listParent = listStyle.Parent;
            listParent.RemoveAllChildren<DocumentFormat.OpenXml.Drawing.Paragraph>();
            foreach(var point in bulletpoints) {
                var paragraph = new DocumentFormat.OpenXml.Drawing.Paragraph();
                paragraph.AppendChild(new DocumentFormat.OpenXml.Drawing.Run(new DocumentFormat.OpenXml.Drawing.Text(point)));
                listParent.AppendChild(paragraph);
            }
        }

        private static void ReplaceParagraphText(ShapeTree tree, int index, string val)
        {
            var phShap = FindPlaceholderShape(tree, index);
            var paragraph = phShap.Parent.Parent.Parent.Descendants<DocumentFormat.OpenXml.Drawing.Paragraph>().FirstOrDefault();
            if (paragraph == null) throw new Exception("Paragraph not found");
            paragraph.RemoveAllChildren();
            paragraph.AppendChild(new DocumentFormat.OpenXml.Drawing.Run(new DocumentFormat.OpenXml.Drawing.Text(val)));
        }

        private static void RemovePlaceholder(ILambdaLogger logger, ShapeTree tree, int index)
        {
            var phShap = FindPlaceholderShape(tree, index);
            phShap.Remove();
        }

        private static void AppendNewPictureInPlaceholder(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, int index, string imagePath, Int64Value width, Int64Value height, bool removeLocation = false)
        {
            var picture = CreatePicture(logger, slide, imagePath, width, height);
            var phShap = FindPlaceholderShape(slide.CommonSlideData.ShapeTree, index);
            var shape = phShap.Parent.Parent.Parent;

            var listToRemove = new List<OpenXmlElement>();
            var addToRemove = false;
            for(var i = 0; i < slide.CommonSlideData.ShapeTree.ChildElements.Count; i++)
            {
                if (shape == slide.CommonSlideData.ShapeTree.ChildElements[i] || addToRemove) {
                    listToRemove.Add(slide.CommonSlideData.ShapeTree.ChildElements[i]);
                    addToRemove = true;
                }
            }
            foreach(var item in listToRemove)
            {
                item.Remove();
            }
            phShap.Remove();
            picture.ChildElements[0].ChildElements[2].AppendChild(phShap);
            slide.CommonSlideData.ShapeTree.AppendChild(picture);
            slide.CommonSlideData.ShapeTree.ChildElements[1].RemoveAllChildren();
            if (removeLocation) {
                picture.ChildElements[2].RemoveAllChildren();
            }
        }

        private static PlaceholderShape FindPlaceholderShape(ShapeTree tree, int idx) {
            var desendants = tree.Descendants<DocumentFormat.OpenXml.Presentation.PlaceholderShape>();
            return desendants.SingleOrDefault(ph => {
                if (ph == null) throw new Exception("Placeholder shape not found asdfsd");
                return (idx == 0 && (ph.Index == null || !ph.Index.HasValue)) || (ph.Index != null && ph.Index.HasValue && ph.Index.Value == idx);
            });
        }

        private static CommonSlideData CreateNotesSlideShapeTree(string notes) 
        {
            var nvgspr = new DocumentFormat.OpenXml.Presentation.NonVisualGroupShapeProperties(
                new DocumentFormat.OpenXml.Presentation.NonVisualDrawingProperties()
                {
                    Id = (UInt32Value)1U,
                    Name = ""
                },
                new DocumentFormat.OpenXml.Presentation.NonVisualGroupShapeDrawingProperties(),
                new DocumentFormat.OpenXml.Presentation.ApplicationNonVisualDrawingProperties());
            
            var gsppr = new GroupShapeProperties(new TransformGroup(
                new DocumentFormat.OpenXml.Drawing.Offset() { X = 0L, Y = 0L },
                new DocumentFormat.OpenXml.Drawing.Extents() { Cx = 0L, Cy = 0L },
                new DocumentFormat.OpenXml.Drawing.ChildOffset() { X = 0L, Y = 0L },
                new DocumentFormat.OpenXml.Drawing.Extents() { Cx = 0L, Cy = 0L }
            ));
            var shape1 = new DocumentFormat.OpenXml.Presentation.Shape(
                new DocumentFormat.OpenXml.Presentation.NonVisualShapeProperties(
                    new DocumentFormat.OpenXml.Presentation.NonVisualDrawingProperties()
                    {
                        Id = (UInt32Value)2U,
                        Name = "Slide Image Placeholder 1"
                    },
                    new DocumentFormat.OpenXml.Presentation.NonVisualShapeDrawingProperties(
                        new DocumentFormat.OpenXml.Drawing.ShapeLocks()
                        {
                            NoGrouping = true
                        }
                    ),
                    new DocumentFormat.OpenXml.Presentation.ApplicationNonVisualDrawingProperties(
                        new DocumentFormat.OpenXml.Presentation.PlaceholderShape() {
                            Index = 2u,
                            Type = PlaceholderValues.SlideImage
                        }
                    )
                ),
                new DocumentFormat.OpenXml.Presentation.ShapeProperties());

            var shape2 = new DocumentFormat.OpenXml.Presentation.Shape(
                new DocumentFormat.OpenXml.Presentation.NonVisualShapeProperties(
                    new DocumentFormat.OpenXml.Presentation.NonVisualDrawingProperties()
                    {
                        Id = (UInt32Value)3U,
                        Name = "Notes Placeholder 2"
                    },
                    new DocumentFormat.OpenXml.Presentation.NonVisualShapeDrawingProperties(
                        new DocumentFormat.OpenXml.Drawing.ShapeLocks()
                        {
                            NoGrouping = true
                        }
                    ),
                    new DocumentFormat.OpenXml.Presentation.ApplicationNonVisualDrawingProperties(
                        new DocumentFormat.OpenXml.Presentation.PlaceholderShape() { Index =3u, Type = PlaceholderValues.Body, Size = PlaceholderSizeValues.Quarter}
                    )
                ),
                new DocumentFormat.OpenXml.Presentation.ShapeProperties(),
                new DocumentFormat.OpenXml.Presentation.TextBody(
                    new BodyProperties(),
                    new ListStyle()
                ));

            foreach(var paragraph in notes.Split("\n"))
            {
                var textBody = shape2.Descendants<DocumentFormat.OpenXml.Presentation.TextBody>().FirstOrDefault();
                if (textBody == null) throw new Exception("Text body not found");
                textBody.AppendChild(
                    new DocumentFormat.OpenXml.Drawing.Paragraph(
                        new DocumentFormat.OpenXml.Drawing.Run(
                            new DocumentFormat.OpenXml.Drawing.Text(paragraph)
                            )
                        )
                    );
            }

            var shape3 = new DocumentFormat.OpenXml.Presentation.Shape(
                new DocumentFormat.OpenXml.Presentation.NonVisualShapeProperties(
                    new DocumentFormat.OpenXml.Presentation.NonVisualDrawingProperties()
                    {
                        Id = (UInt32Value)4U,
                        Name = "Slide Number Placeholder 3"
                    },
                    new DocumentFormat.OpenXml.Presentation.NonVisualShapeDrawingProperties(
                        new DocumentFormat.OpenXml.Drawing.ShapeLocks()
                        {
                            NoGrouping = true
                        }
                    ),
                    new DocumentFormat.OpenXml.Presentation.ApplicationNonVisualDrawingProperties(
                        new DocumentFormat.OpenXml.Presentation.PlaceholderShape() {
                            Index = 5u, Type = PlaceholderValues.SlideNumber, Size = PlaceholderSizeValues.Quarter
                        }
                    )
                ),
                new DocumentFormat.OpenXml.Presentation.ShapeProperties());
            
            return new CommonSlideData(
                new ShapeTree(
                    nvgspr,
                    gsppr,
                    shape1,
                    shape2,
                    shape3));
        }

        private static void AddSpeakerNotes(ILambdaLogger logger, DocumentFormat.OpenXml.Presentation.Slide slide, string notes, DocumentFormat.OpenXml.Office2021.PowerPoint.Comment.Author author, uint slideId) {
            var notesPart = slide.SlidePart.NotesSlidePart ?? slide.SlidePart.AddNewPart<NotesSlidePart>();
            if (notesPart.NotesSlide == null) {
                var nodeSlide = new DocumentFormat.OpenXml.Presentation.NotesSlide(CreateNotesSlideShapeTree(notes));
                notesPart.NotesSlide = nodeSlide;
            } else {
                var paragraph = notesPart.NotesSlide.CommonSlideData.ShapeTree.Descendants<DocumentFormat.OpenXml.Drawing.Paragraph>().FirstOrDefault();
                if (paragraph == null) throw new Exception("Paragraph for notes not found");
                paragraph.RemoveAllChildren();
                paragraph.AppendChild(new DocumentFormat.OpenXml.Drawing.Run(new DocumentFormat.OpenXml.Drawing.Text(notes)));

            }
        }

        private static async Task CreateSlide(ILambdaLogger logger, PowerPointContext context, PresentationDocument doc, JsonArray slides, JsonObject slide_json, JsonObject mappings, SlideLayoutPart slideLayoutPart, int slideIdx)
        {
            var slidePart = doc.PresentationPart.AddNewPart<SlidePart>();
            var slide = new DocumentFormat.OpenXml.Presentation.Slide();
            slide.CommonSlideData = (CommonSlideData)slideLayoutPart.SlideLayout.CommonSlideData.Clone();
            slide.ColorMapOverride = (ColorMapOverride)slideLayoutPart.SlideLayout.ColorMapOverride.Clone();
            slidePart.AddPart<DocumentFormat.OpenXml.Packaging.SlideLayoutPart>(slideLayoutPart);
            slidePart.Slide = slide;

            var slideFormat = slide_json["slideFormat"]!.GetValue<string>();
            var mapping = mappings[slideFormat]!.AsObject();
            var size = doc.PresentationPart.Presentation.SlideSize;
            var slideId = AppendSlide(doc, slidePart);

            Console.WriteLine("slide format: " + slideFormat);
            Console.WriteLine("slide index: " + slideIdx);
            switch(slideFormat)
            {
                case "Title page":
                    CreateTitlePage(logger, context, slide, size, slide_json, mapping);
                    break;
                case "Agenda":
                    CreateAgenda(logger, slide, slide_json, slides.Select(s => s["title"]?.GetValue<string>()).Where(s => !string.IsNullOrEmpty(s)).ToArray(), mapping);
                    break;
                case "Slide with bullet points":
                    CreateBulletPoints(logger, slide, slide_json, mapping);
                    break;
                case "Slide with image and text":
                    await CreateImageAndText(logger, slide, context.ReferenceId, slideId, size, slide_json, mapping);
                    break;
                case "Slide with image only":
                    await CreateWithImageOnly(logger, slide, context.ReferenceId, slideId, size, slide_json, mapping);
                    break;
                case "Slide with 4 takeaways":
                    CreateTakeaways(logger, slide, slide_json, mapping);
                    break;
                case "Thank you":
                    CreateThankyou(logger, context, slide, size, slide_json, mapping);
                    break;
                default:
                    throw new Exception("Unknown slide format: " + slideFormat);
            }

            // AppendSlide(doc, slidePart);

            // var author = doc.PresentationPart.authorsPart.AuthorList
                // .ChildElements.OfType<DocumentFormat.OpenXml.Office2021.PowerPoint.Comment.Author>().First();
            var notes = slide_json["speaker_notes"]?.GetValue<string>() ?? "";
            notes = string.Format("Speaker  Notes: \n{0}", notes);
            AddSpeakerNotes(logger, slide, notes, null, slideId);
        }

        private static uint AppendSlide(PresentationDocument doc, SlidePart slidePart)
        {

            SlideIdList slideIdList = doc.PresentationPart.Presentation.SlideIdList;
            var max = slideIdList.GetMaxSlideId() + 256u;
            slideIdList.AppendChild(new SlideId() { Id = max, RelationshipId = doc.PresentationPart.GetIdOfPart(slidePart) });
            return max;
        }

        private static void ValidateSlide(ILambdaLogger logger, JsonObject slide_json) 
        {
            var properties = new Dictionary<string, int>() {
                {"title", 50},
                {"subtitle", 75},
                {"speaker_notes", 500},
                {"text", 300}
            
            };
            var format = slide_json["slideFormat"]?.GetValue<string>(); 

            if (string.IsNullOrEmpty(format)) throw new Exception("slideFormat is required");

            foreach(var property in properties)
            {
                var value = slide_json[property.Key]?.GetValue<string>();
                if (value != null && value.Length > property.Value)
                {
                    value = value.Substring(0, property.Value);
                    logger.LogDebug("{0} trimmed down: {1}", property.Key, value);
                    slide_json[property.Key] = value;
                }
            }

        }

        public static async Task<string> GeneratePowerPoint(ILambdaLogger logger, PowerPointContext context, JsonArray slides)
        {
            var tmpfolder = System.IO.Path.Combine(OUTPUT_FOLDER, context.ReferenceId.ToString("D"));
            logger.LogDebug("tmpfolder: {0}", tmpfolder);
            Directory.CreateDirectory(tmpfolder);
            var mappings = JsonSerializer.Deserialize<JsonObject>(File.ReadAllText(context.MappingJsonPath));
            var referenceId = Guid.NewGuid().ToString();
            var filePath = System.IO.Path.Combine(OUTPUT_FOLDER, context.ReferenceId.ToString(), string.Format("{0}_generated.pptx", referenceId));

            File.Copy(context.TemplatePath, filePath, true);

            using (var template = PresentationDocument.Open(filePath, true, new OpenSettings() { AutoSave = false}))
            {
                var i = 1;

                logger?.LogInformation("master count: " + template.PresentationPart.SlideMasterParts.Count());

                var master = template.PresentationPart.SlideMasterParts.First();
                template.PresentationPart.Presentation.SlideIdList = new SlideIdList();

                foreach(var slide_json in slides) {
                    ValidateSlide(logger, slide_json.AsObject());
                    var slide_format = slide_json["slideFormat"].GetValue<string>();
                    var layout_name = mappings[slide_format]["layout_slide_name"].GetValue<string>();
                    var slideLayoutPart = master.SlideLayoutParts.SingleOrDefault(p => p.SlideLayout.CommonSlideData.Name.Value.Equals(layout_name, StringComparison.InvariantCultureIgnoreCase));
                    if (slideLayoutPart == null) throw new Exception("layout not found: " + layout_name);

                    await CreateSlide(logger, context, template, slides, slide_json.AsObject(), mappings, slideLayoutPart, i);
                    i++;
                }
                template.Save();
                template.Dispose();
            }

            return filePath;
        }

        private static uint GetMaxSlideId(this SlideIdList slideIdList)
        {
            // find the highest id
            uint maxSlideId = 0;
            if (slideIdList.ChildElements.Count() > 0)
                maxSlideId = slideIdList.ChildElements
                    .Cast<SlideId>()
                    .Max(x => x.Id.Value);
            return maxSlideId;
        }
    }
}