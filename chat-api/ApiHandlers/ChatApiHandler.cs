using Microsoft.Extensions.Logging;
using ApiHandlers.Models;
using ApiHandlers.DataAccess;
using Microsoft.AspNetCore.Http;
using System.Security.Claims;
using Amazon.BedrockAgentRuntime.Model;
using Microsoft.Extensions.Options;
using ApiHandlers.Options;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
using System.Text.Json.Serialization;
using System.Text.Json;

namespace ApiHandlers
{
    public partial class ChatApiHandler(ILogger<ChatApiHandler> logger, ChatDAO chats, IHttpContextAccessor contextAccessor, AWSClientFactory factory, IOptions<RedrockAgentConfig> agentConfig)
    {
        [GeneratedRegex(@"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", RegexOptions.IgnoreCase)]
        private static partial Regex TenantIdRegex();

        [GeneratedRegex(@"^[0-9a-zA-Z._:\-]{2,100}$")]
        private static partial Regex SessionIdRegex();

        private static bool IsValidTenantId(string tenantId) =>
            !string.IsNullOrWhiteSpace(tenantId) && TenantIdRegex().IsMatch(tenantId);

        private static bool IsValidSessionId(string sessionId) =>
            !string.IsNullOrWhiteSpace(sessionId) && SessionIdRegex().IsMatch(sessionId);

        private async Task<Chat> GetAndVerify(string tenantId, string sessionId) {
            var chat = await chats.Get(sessionId);
            if (chat == null) {
                throw new ResourceNotFoundException("Chat with matching sessionId is not found.");
            } else if (chat.TenantId != tenantId) {
                throw new ConflictException("TenaltId doesn't match");
            }
            return chat;
        
        }
        public async Task<IResult> GetSession([FromRoute]string tenantId, [FromRoute]string sessionId)
        {
            logger.LogInformation("Received request to get chat history by session id");

            if (!IsValidTenantId(tenantId))
                return Results.BadRequest("Invalid tenant ID format.");
            if (!IsValidSessionId(sessionId))
                return Results.BadRequest("Invalid session ID format.");

            try
            {
                var chat = await GetAndVerify(tenantId, sessionId);
                return Results.Ok(chat);
            }

            catch (ResourceNotFoundException)
            {
                logger.LogWarning("SessionId: {sessionId} not found", sessionId);
                return Results.NotFound();
            }

            catch (ConflictException)
            {
                logger.LogWarning("TenantId: {tenantId} doesn't match", tenantId);
                return Results.Forbid();
            }        
        }

        public async Task<IResult> StartSession([FromRoute]string tenantId)
        {
            logger.LogInformation("Received request to start a new chat session for {tenantId}", tenantId);

            if (!IsValidTenantId(tenantId))
                return Results.BadRequest("Invalid tenant ID format.");

            try {
                
                var userId = contextAccessor?.HttpContext?.User.FindFirstValue("sub") ?? "n/a" ;
                var agent = await factory.GetBedrockAgentRuntimeAsync(tenantId);
                logger.LogDebug("Starting new session for userId: {userId}...", userId);
                var session = await agent.CreateSessionAsync(new CreateSessionRequest() {
                    SessionMetadata = new Dictionary<string, string>() {
                        { "tenantId", tenantId },
                        { "userId", userId }
                    }
                });
                logger.LogDebug("Session started: {sessionId}, {sessionStatus}", session.SessionId, session.SessionStatus);
                var chat = new Chat(tenantId, session.SessionId, session.SessionStatus, userId, []);        

                await chats.Post(chat);
                return Results.Json(chat);
            } catch (Exception e) {
                logger.LogError(e, "Failed to start a new session");
                return Results.Problem();
            }
        }

        public async Task<IResult> SubmitPrompt([FromRoute]string tenantId, [FromRoute]string sessionId, [FromBody]Prompt prompt)
        {
            logger.LogInformation("Received request to submit a prompt for {tenantId} and {sessionId}", tenantId, sessionId);

            if (!IsValidTenantId(tenantId))
                return Results.BadRequest("Invalid tenant ID format.");
            if (!IsValidSessionId(sessionId))
                return Results.BadRequest("Invalid session ID format.");

            var output = new MemoryStream();

            try
            {
                logger.LogDebug("agent config: {agentId} {agentAliasId}", agentConfig.Value.AgentId, agentConfig.Value.AgentAliasId);
                var chat = await GetAndVerify(tenantId, sessionId);
                var agent = await factory.GetBedrockAgentAsync(chat.TenantId);
                var list = await agent.ListKnowledgeBasesAsync(new Amazon.BedrockAgent.Model.ListKnowledgeBasesRequest() {

                });
                logger.LogDebug("Number of knowledge: {num}", list.KnowledgeBaseSummaries.Count);
                var kb = list.KnowledgeBaseSummaries.FirstOrDefault(item => item.Name.Contains(chat.TenantId, StringComparison.InvariantCultureIgnoreCase));

                if (kb == null) new ConflictException("Unable to find Knowledge base for this tenant");

                var promptText = $"prompt = '{prompt.Text}', kd_id = '{kb?.KnowledgeBaseId}', tenant_id = '{tenantId}'";
                logger.LogDebug("prompt: {promptText}", promptText);

                var agentRuntime = await factory.GetBedrockAgentRuntimeAsync(chat.TenantId);                
                var response = await agentRuntime.InvokeAgentAsync(new InvokeAgentRequest() {
                    AgentId = agentConfig.Value.AgentId,
                    AgentAliasId = agentConfig.Value.AgentAliasId,
                    SessionId = chat.SessionId,    
                    SessionState = new SessionState() {
                        SessionAttributes = new Dictionary<string, string>() {
                            { "tenantId", chat.TenantId },
                            { "userId", chat.UserId }
                        }

                    },
                    InputText = promptText,
                    EnableTrace = true,
                    
                });
                logger.LogDebug("Result: {statusCode}", response.HttpStatusCode);
                logger.LogDebug("Result: {metadata}", JsonSerializer.Serialize(response.ResponseMetadata));
                foreach(var item in response.Completion)
                {
                    
                    var trace = item as TracePart;
                    var payload = item as PayloadPart;
                    var files = item as FilePart; //Amazon.BedrockAgentRuntime.Model.Part
                    var chunks = item as TextResponsePart;
                    var test = item as Amazon.BedrockAgentRuntime.Model.InlineAgentPayloadPart;
                    
                    if (trace != null) {                        
                        output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(trace).ToCharArray()));
                    } else if (files != null) {                        
                        output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(files).ToCharArray()));
                    } else if (payload != null) {
                        if (payload.Attribution?.Citations != null) {
                            foreach(var citation in payload.Attribution.Citations) {
                                if (citation.GeneratedResponsePart?.TextResponsePart != null)
                                    output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(citation.GeneratedResponsePart.TextResponsePart)));
                                if (citation.RetrievedReferences != null && citation.RetrievedReferences.Count > 0) {
                                    foreach(var reference in citation.RetrievedReferences) {
                                        output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(reference.Content)));
                                        output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(reference.Location)));
                                    }
                                }
                            }
                            output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(chunks).ToCharArray()));
                        }
                        output.Write(payload.Bytes.ToArray());
                    } else {
                        logger.LogDebug("Unknown part: {item}", item.GetType());
                        output.Write(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(item).ToCharArray()));
                    }
                }


                var result = Encoding.UTF8.GetString(output.ToArray());
                logger.LogDebug("Finished receiving results: " + result);
                prompt.Response = result;
                chat.Prompts.Append(prompt);
                await chats.Post(chat);
                logger.LogDebug("Updated");
                
                if(response.HttpStatusCode == System.Net.HttpStatusCode.OK)
                {
                    return Results.Content(result);
                }
                else 
                {
                    return Results.BadRequest(result);
                }
            }

            catch (ResourceNotFoundException e)
            {
                logger.LogError(e, "Chat with matching sessionId is not found.");
                logger.LogWarning("SessionId: {sessionId} not found", sessionId);
                return Results.NotFound();
            }

            catch (ConflictException e)
            {
                logger.LogError(e, "Tenant is not set up correctly");
                logger.LogWarning("TenantId: {tenantId} doesn't match", tenantId);
                return Results.Forbid();
            }   

            catch (Exception e)
            {
                logger.LogDebug("Payload received: " + Encoding.UTF8.GetString(output.ToArray()));
                logger.LogError(e, "Error occurred while processing this prompt for session {sessionId}", sessionId);
                return Results.Problem(e.Message);
            }     

            finally {
                output.Dispose();
            }

        }

        public async Task<IResult> EndSession([FromRoute]string tenantId, [FromRoute]string sessionId)
        {
            logger.LogInformation("Received request to end chat history");

            if (!IsValidTenantId(tenantId))
                return Results.BadRequest("Invalid tenant ID format.");
            if (!IsValidSessionId(sessionId))
                return Results.BadRequest("Invalid session ID format.");

            // await chats.Delete(sessionId);
            try
            {
                var chat = await GetAndVerify(tenantId, sessionId);
                var agent = await factory.GetBedrockAgentRuntimeAsync(chat.TenantId);
                var response = await agent.EndSessionAsync(new EndSessionRequest() {
                    SessionIdentifier = sessionId
                });
                chat.SessionStatus = "Ended";
                await chats.Put(sessionId, chat);
                if (response.HttpStatusCode == System.Net.HttpStatusCode.OK){
                    return Results.Ok();
                }
                else {
                    return Results.Problem($"Unable to end session {sessionId}");
                }
            }

            catch (ResourceNotFoundException)
            {
                logger.LogWarning("SessionId: {sessionId} not found", sessionId);
                return Results.NotFound();
            }

            catch (ConflictException)
            {
                logger.LogWarning("TenantId: {tenantId} doesn't match", tenantId);
                return Results.Forbid();
            }  

            catch (Exception e)
            {
                logger.LogError(e, "Failed to end this session {sessionId}", sessionId);
                return Results.Problem();
            }       
            
        }
    }
}