using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.Builder;

namespace ApiHandlers
{
    public static class RouteGroupBuilderExtension
    {
        public static RouteGroupBuilder MapChatApi(this RouteGroupBuilder group, ChatApiHandler handler)
        {
            group.MapPost("/tenants/{tenantId}/sessions", handler.StartSession);
            group.MapGet("/tenants/{tenantId}/sessions/{sessionId}", handler.GetSession);
            group.MapPut("/tenants/{tenantId}/sessions/{sessionId}/prompts", handler.SubmitPrompt);
            group.MapDelete("/tenants/{tenantId}/sessions/{sessionId}", handler.EndSession);

            return group;
        }
    }
}