namespace ApiHandlers.Options;

public class RedrockAgentConfig
{
    /// <summary>
    /// The AgentCore Runtime Endpoint ARN used to invoke the agent.
    /// Replaces the classic AgentId + AgentAliasId pattern.
    /// </summary>
    public required string AgentRuntimeEndpointArn { get; set; }
    
    public string? ModelId { get; set; }
}
