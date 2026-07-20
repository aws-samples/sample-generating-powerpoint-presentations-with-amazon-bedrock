using Microsoft.Extensions.Logging;
using ApiHandlers.Models;
using ApiHandlers.DataAccess;
using Microsoft.AspNetCore.Http;
using System.Security.Claims;
using Amazon.BedrockAgentCore;
using Amazon.BedrockAgentCore.Model;
using Microsoft.Extensions.Options;
using ApiHandlers.Options;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
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
                throw new ConflictException("TenantId doesn't match");
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
                var userId = contextAccessor?.HttpContext?.User.FindFirstValue("sub") ?? "n/a";
                
                // For AgentCore Runtime, sessions are managed by the runtime itself.
                // We generate a session ID client-side and pass it as RuntimeSessionId
                // on each InvokeAgentRuntime call. The runtime handles session lifecycle.
                var sessionId = Guid.NewGuid().ToString();
                
                logger.LogDebug("Starting new session {sessionId} for userId: {userId}", sessionId, userId);
                var chat = new Chat(tenantId, sessionId, "Active", userId, []);        

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
                var endpointArn = agentConfig.Value.AgentRuntimeEndpointArn;
                logger.LogDebug("AgentCore endpoint: {endpointArn}", endpointArn);
                
                var chat = await GetAndVerify(tenantId, sessionId);
                
                // Look up the tenant's knowledge base
                var agent = await factory.GetBedrockAgentAsync(chat.TenantId);
                var list = await agent.ListKnowledgeBasesAsync(new Amazon.BedrockAgent.Model.ListKnowledgeBasesRequest());
                logger.LogDebug("Number of knowledge bases: {num}", list.KnowledgeBaseSummaries.Count);
                var kb = list.KnowledgeBaseSummaries.FirstOrDefault(item => item.Name.Contains(chat.TenantId, StringComparison.InvariantCultureIgnoreCase));

                if (kb == null) throw new ConflictException("Unable to find Knowledge base for this tenant");

                // Build the prompt with parameters for the agent
                var promptText = $"prompt = '{prompt.Text}', kb_id = '{kb?.KnowledgeBaseId}', tenant_id = '{tenantId}'";
                logger.LogDebug("prompt: {promptText}", promptText);

                // Build the payload as JSON
                var payloadJson = JsonSerializer.Serialize(new { text = promptText });
                var payloadStream = new MemoryStream(Encoding.UTF8.GetBytes(payloadJson));

                // Invoke the AgentCore Runtime agent
                var agentCore = await factory.GetBedrockAgentCoreAsync(chat.TenantId);
                
                var response = await agentCore.InvokeAgentRuntimeAsync(new InvokeAgentRuntimeRequest() {
                    AgentRuntimeArn = endpointArn,
                    RuntimeSessionId = chat.SessionId,
                    Payload = payloadStream,
                    ContentType = "application/json",
                    Accept = "application/json"
                });
                
                logger.LogDebug("Result: {statusCode}", response.HttpStatusCode);

                // Read the streaming response
                if (response.Response != null)
                {
                    await response.Response.CopyToAsync(output);
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
            logger.LogInformation("Received request to end chat session");

            if (!IsValidTenantId(tenantId))
                return Results.BadRequest("Invalid tenant ID format.");
            if (!IsValidSessionId(sessionId))
                return Results.BadRequest("Invalid session ID format.");

            try
            {
                var chat = await GetAndVerify(tenantId, sessionId);
                
                // For AgentCore Runtime, session cleanup is handled by the runtime
                // when the session TTL expires. We just update our local state.
                chat.SessionStatus = "Ended";
                await chats.Put(sessionId, chat);
                return Results.Ok();
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
