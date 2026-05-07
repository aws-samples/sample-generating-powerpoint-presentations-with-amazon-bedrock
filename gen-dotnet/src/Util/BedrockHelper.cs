using Amazon.BedrockAgentRuntime;
using Amazon.BedrockAgentRuntime.Model;
using Amazon.BedrockRuntime;
using Amazon.BedrockRuntime.Model;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Amazon.Lambda.Core;
using System.Security.Cryptography;
using Amazon.Bedrock;
using Amazon.Bedrock.Model;
using System.Security;

namespace AgentProxy.Util {
    public static class BedrockHelper {
        private static readonly string OUTPUT_FOLDER = "/tmp";
        private static GuardrailContentPolicyConfig DefaultGuardrailContentPolicy() {
            var policy = new GuardrailContentPolicyConfig();
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.SEXUAL, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.VIOLENCE, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.HATE, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.INSULTS, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.MISCONDUCT, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            policy.FiltersConfig.Add(new GuardrailContentFilterConfig() {Type = Amazon.Bedrock.GuardrailContentFilterType.PROMPT_ATTACK, InputStrength = GuardrailFilterStrength.LOW, OutputStrength = GuardrailFilterStrength.LOW});
            return policy;
        }

        private static GuardrailSensitiveInformationPolicyConfig DefaultGuardrailInfoPolicy() {
            var policy = new GuardrailSensitiveInformationPolicyConfig();

            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.EMAIL, Action = GuardrailSensitiveInformationAction.ANONYMIZE});
            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.PHONE, Action = GuardrailSensitiveInformationAction.ANONYMIZE});
            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.NAME, Action = GuardrailSensitiveInformationAction.ANONYMIZE});
            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.US_SOCIAL_SECURITY_NUMBER, Action = GuardrailSensitiveInformationAction.BLOCK});
            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.US_BANK_ACCOUNT_NUMBER, Action = GuardrailSensitiveInformationAction.BLOCK});
            policy.PiiEntitiesConfig.Add(new GuardrailPiiEntityConfig() { Type = Amazon.Bedrock.GuardrailPiiEntityType.CREDIT_DEBIT_CARD_NUMBER, Action = GuardrailSensitiveInformationAction.BLOCK});

            policy.RegexesConfig = new List<GuardrailRegexConfig>() {
                new GuardrailRegexConfig() {
                    Name="Account Number",
                    Description="Matches account numbers in the format XXXXXX1234",
                    Pattern="\\b\\d{6}\\d{4}\\b",
                    Action= GuardrailSensitiveInformationAction.ANONYMIZE
                }
            };

            return policy;
        
        }


        /// <summary>
        /// DEPRECATED: Guardrails are now provisioned via CDK (CfnGuardrail).
        /// This method is retained only as a fallback and should not be used in production.
        /// Guardrail ID and version are read from GUARDRAIL_ID and GUARDRAIL_VERSION environment variables.
        /// </summary>
        [Obsolete("Guardrails are now CDK-managed. Use GUARDRAIL_ID/GUARDRAIL_VERSION env vars instead.")]
        public static async Task<(string?, string?)> EnsureGuardrail(
            ILambdaLogger logger, 
            string name = "", 
            string description="", 
            string blockedInputMessage="I cannot process your request due to security restrictions on the input content.",
            string blockedOutputMessage="I cannot provide the requested information due to security restrictions.") {

            try {
                var client = new AmazonBedrockClient();

                logger.LogInformation("Look up existing guardrail matching this name: {0}", name);

                var response = await client.ListGuardrailsAsync(new ListGuardrailsRequest() {MaxResults=123}); //to-do: implement paging to search for an existing guardrailId
                
                if (response.HttpStatusCode != System.Net.HttpStatusCode.OK) {
                    throw new Exception($"Failed to list guardrails: {response.HttpStatusCode}");
                }

                var guardrail = response.Guardrails.FirstOrDefault(g => g.Name == name);

                if (guardrail == null) {
                    logger.LogInformation("No guardrail found, creating a new one");
                    var createResponse = await client.CreateGuardrailAsync(new CreateGuardrailRequest() {
                        Name = name,
                        Description = description,
                        ContentPolicyConfig = DefaultGuardrailContentPolicy(),
                        SensitiveInformationPolicyConfig = DefaultGuardrailInfoPolicy(),
                        BlockedInputMessaging = blockedInputMessage,
                        BlockedOutputsMessaging = blockedOutputMessage,
                        Tags = new List<Amazon.Bedrock.Model.Tag>() { new Amazon.Bedrock.Model.Tag() {Key = "purpose", Value = name}, new Amazon.Bedrock.Model.Tag() {Key = "environment", Value = Environment.GetEnvironmentVariable("Environment") ?? "Unspecified"}}
                    });
                    if (createResponse.HttpStatusCode != System.Net.HttpStatusCode.OK) {
                        throw new Exception($"Failed to create guardrails: {createResponse.HttpStatusCode}");
                    }
                    return (createResponse.GuardrailId, createResponse.Version);
                }
                else {
                    logger.LogDebug("GuardrailId found: {0} - {1}", guardrail.Id, guardrail.Version);
                    return (guardrail.Id, guardrail.Version);
                }
            }
            catch (Exception e)
            {
                logger.LogError(e, "Failed to obtain guardrails");
                return (null, null);
            }
        }

        public static async Task<(JsonNode?, JsonNode?)> invoke_llm_text(ILambdaLogger logger, string prompt, string modelId = "anthropic.claude-3-sonnet-20240229-v1:0", int max_tokens = 512) {
            var modelInput = new
            {
                anthropic_version = "bedrock-2023-05-31",
                max_tokens,
                messages = new[]
                {
                    new { role = "user", content = prompt }
                }
            };
            var json = JsonSerializer.Serialize(modelInput);
            logger.LogDebug("Invoke Model Input: {0}", json);
            var body = new MemoryStream(Encoding.UTF8.GetBytes(json));
            var jobj = await invoke_bedrock_model(logger, modelId, body);
            return (jobj["content"], jobj["usage"]);
        }
        public static async Task<RetrieveAndGenerateResponse> generate_text_kb(ILambdaLogger logger, Models.SessionState ss, int numberOfVectorSearchResults = 7)  {
            return await generate_text_kb(logger, ss.topic, ss.kb_id, ss.chosen_llm, 4096 / 15 * ss.n_slides, "kb-pptx-guardrail", numberOfVectorSearchResults);
        }
    
        public static async Task<RetrieveAndGenerateResponse> generate_text_kb(ILambdaLogger logger, string topic, string kb_id, string model_id, int? max_tokens, string guardrailName, int numberOfVectorSearchResults = 7)  {
            //"""retrieve information from knowledge base and generate a response from bedrock LLM
            // var prompt = Encoding.ASCII.GetString(Encoding.Convert(Encoding.ASCII, Encoding.UTF8, Encoding.UTF8.GetBytes(topic)));
            // var max_tokens = 4096 / 15 * N_SLIDES;
            var region = System.Environment.GetEnvironmentVariable("AWS_REGION");

            logger.LogDebug("Invoke BedrockAgentRuntime.RetrieveAndGenerateAsync with: {0}", topic);
            logger.LogDebug("max_tokens: {0}", max_tokens);

            var client = new AmazonBedrockAgentRuntimeClient();
            var guardrailId = System.Environment.GetEnvironmentVariable("GUARDRAIL_ID");
            var guardrailVersion = System.Environment.GetEnvironmentVariable("GUARDRAIL_VERSION");
            var input = new RetrieveAndGenerateInput {
                    Text = topic
                };
            var generalConfig = new Amazon.BedrockAgentRuntime.Model.GenerationConfiguration {                            
                            InferenceConfig = new InferenceConfig {
                                TextInferenceConfig = max_tokens != null ? new Amazon.BedrockAgentRuntime.Model.TextInferenceConfig {
                                    MaxTokens = max_tokens.Value,
                                } : null
                            
            }};
            if (!string.IsNullOrEmpty(guardrailId) && !string.IsNullOrEmpty(guardrailVersion)) {
                generalConfig.GuardrailConfiguration = new Amazon.BedrockAgentRuntime.Model.GuardrailConfiguration {
                    GuardrailId = guardrailId,
                    GuardrailVersion = guardrailVersion
                };
            } else {
                logger.LogWarning("GUARDRAIL_ID or GUARDRAIL_VERSION not set. Proceeding without guardrails.");
            }

            var ragConfig = new Amazon.BedrockAgentRuntime.Model.RetrieveAndGenerateConfiguration {
                    Type = Amazon.BedrockAgentRuntime.RetrieveAndGenerateType.FindValue("KNOWLEDGE_BASE"),
                    KnowledgeBaseConfiguration = new Amazon.BedrockAgentRuntime.Model.KnowledgeBaseRetrieveAndGenerateConfiguration {
                        KnowledgeBaseId = kb_id,
                        ModelArn = string.Format("arn:aws:bedrock:{0}::foundation-model/{1}",region, model_id),
                        GenerationConfiguration = generalConfig,
                        RetrievalConfiguration = new Amazon.BedrockAgentRuntime.Model.KnowledgeBaseRetrievalConfiguration {
                            VectorSearchConfiguration = new Amazon.BedrockAgentRuntime.Model.KnowledgeBaseVectorSearchConfiguration {
                                NumberOfResults = numberOfVectorSearchResults
                            }
                        }
                    }
                };
            var response = await client.RetrieveAndGenerateAsync(new RetrieveAndGenerateRequest() {
                Input = input,
                RetrieveAndGenerateConfiguration = ragConfig
            });

            if (response.HttpStatusCode != System.Net.HttpStatusCode.OK) {
                throw new Exception($"Error generate_text_kb: {response.HttpStatusCode}");
            }

            logger.LogDebug("Raw RAG output: {0}", response.Output.Text);

            if (response.GuardrailAction == GuadrailAction.INTERVENED) {
                logger.LogWarning("Guardrail action: INTERVENED. Response may be incomplete or filtered.");                
            }

            logger.LogDebug("Generated Response. Length: {0}, Citations: {1}", response.Output.Text.Length, response.Citations.Count);


            return response;
        }

        public static async Task<(JsonNode?, JsonNode?)> generate_text(ILambdaLogger logger, string prompt, int n_slides = 1, string modelId = "anthropic.claude-3-sonnet-20240229-v1:0") {
            var max_tokens = (4096 / 15) * n_slides;
            var modelInput = new
            {
                anthropic_version = "bedrock-2023-05-31",
                max_tokens,
                messages = new[]
                {
                    new { role = "user", content = prompt }
                }
            };
            var json = JsonSerializer.Serialize(modelInput);
            logger!.LogDebug("Invoke Model Input: {0}", json);
            var body = new MemoryStream(Encoding.UTF8.GetBytes(json));
            var jobj = await invoke_bedrock_model(logger, modelId, body);
            var input_tokens = jobj["usage"]!["input_tokens"];
            var output_tokens = jobj["usage"]!["output_tokens"];
            var output_list = jobj["content"] as JsonArray;
            var invocation_details = new {
                    Max_tokens = max_tokens,
                    Input_token_length = input_tokens,
                    Output_token_length = output_tokens,
                    Responses_Returned = output_list!.Count,
            };
            logger!.LogDebug("Invoke details: {0}", JsonSerializer.Serialize(invocation_details));
            return (jobj["content"], jobj["usage"]);
        }

        public static async Task<JsonNode> invoke_bedrock_model(ILambdaLogger logger, string model_id, MemoryStream body) {
            try {
                var bedrock_client = new AmazonBedrockRuntimeClient();
                var response = await bedrock_client.InvokeModelAsync(new InvokeModelRequest {
                    ModelId = model_id,
                    Body = body,
                    ContentType = "application/json",
                    Accept = "application/json"
                });

                if (response.HttpStatusCode != System.Net.HttpStatusCode.OK) {
                    throw new Exception($"Error invoking LLM: {response.HttpStatusCode}");
                }

                using (var reader = new StreamReader(response.Body)) {
                    var json = await reader.ReadToEndAsync();
                    logger.LogDebug("Raw: {0}", json);
                    var result = JsonNode.Parse(json);
                    if (result == null) {
                        throw new Exception("Error parsing JSON response");
                    }
                    return result;
                }
            }
            catch (Exception e) {
                logger.LogError(e, "Error invoking Bedrock model");
                throw;
            }
        }
        
        public static byte[] process_image_response(ILambdaLogger logger, JsonNode response) {
            try {
                var error = response["error"];
                if (error != null) {
                    throw new Exception($"Error generating image: {error}");
                }

                var base64_image = response["images"]![0]!.GetValue<string>();
                return Convert.FromBase64String(base64_image);
            }
            catch (Exception e) {
                logger.LogError(e, $"Error processing image: {e}");
                throw;
            }
        }

        public static async Task<byte[]> generate_image(ILambdaLogger logger, string model_id, string payload) {
            try {
                logger.LogDebug("Generate Image Input: {0}", payload);
                var body = new MemoryStream(Encoding.UTF8.GetBytes(payload));
                var response = await invoke_bedrock_model(logger, model_id, body);
                return process_image_response(logger, response);
            }
            catch (Exception e) {
                logger.LogError(e, $"Error generating image: {e.Message}");
                throw;
            }
        }

        public static async Task<string> generate_bedrock_image(ILambdaLogger logger, string prompt, int image_height, int image_width, string filename, bool high_res_images = false, string model_id = "amazon.titan-image-generator-v1") {
            var quality = high_res_images ? "premium" : "standard";
            var randomBytes = new byte[4];
            var seed = 0u;

            using (var rng = RandomNumberGenerator.Create())
            {
                rng.GetBytes(randomBytes);
                seed = 100;//BitConverter.ToUInt32(randomBytes, 0);
            }
            var payload = new {
                taskType = "TEXT_IMAGE",
                textToImageParams = new {
                    text = prompt
                },
                imageGenerationConfig = new {
                    numberOfImages = 1,
                    quality,
                    height = image_height,
                    width = image_width,
                    cfgScale = 8.0,
                    seed
                }
            };
            try {
                var image_bytes = await generate_image(logger, model_id, JsonSerializer.Serialize(payload));
                var path = Path.Combine(OUTPUT_FOLDER, filename);
                string fullPath = Path.GetFullPath(path);
                if (!fullPath.StartsWith(Path.GetFullPath(OUTPUT_FOLDER)) || fullPath.Contains(".."))
                {
                    throw new SecurityException("Invalid file path detected.");
                }
                File.WriteAllBytes(fullPath, image_bytes);
                logger?.LogDebug("Image saved to: {0}", fullPath);
                return fullPath;
            }
            catch (Exception e) {
                logger.LogError(e, $"Error generating image: {e.Message}");
                throw;
            }

        }
    }
}

