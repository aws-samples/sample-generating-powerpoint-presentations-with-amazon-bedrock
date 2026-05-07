using Amazon.Bedrock;
using Amazon.BedrockAgent;
using Amazon.BedrockAgentRuntime;
using Amazon.BedrockRuntime;
using Amazon.SecurityToken;
using Amazon.SecurityToken.Model;

namespace ApiHandlers;

public class AWSClientFactory () {

    private async Task<Credentials> AssumeRoleForTenantAsync(string tenantId) {
        var sts = new AmazonSecurityTokenServiceClient();
        var request = new AssumeRoleRequest()
        {
            RoleArn = $"arn:{Environment.GetEnvironmentVariable("PARTITION")}:iam::{Environment.GetEnvironmentVariable("ACCOUNTID")}:role/tenant-role-{tenantId}",
            RoleSessionName = $"tenant-session-{tenantId}",
            DurationSeconds = 900
        };
        var response = await sts.AssumeRoleAsync(request);
        return response.Credentials;
    }

    public async Task<IAmazonBedrockRuntime> GetBedrockRuntimeAsync(string tenantId) {
        var credentials = await AssumeRoleForTenantAsync(tenantId);
        return new AmazonBedrockRuntimeClient(credentials);
    }

    public async Task<IAmazonBedrockAgentRuntime> GetBedrockAgentRuntimeAsync(string tenantId) {
        var credentials = await AssumeRoleForTenantAsync(tenantId);
        return new AmazonBedrockAgentRuntimeClient(credentials);
    }

    public async Task<IAmazonBedrockAgent> GetBedrockAgentAsync(string tenantId) {
        var credentials = await AssumeRoleForTenantAsync(tenantId);
        return new AmazonBedrockAgentClient(credentials);
    }

    public async Task<IAmazonBedrock> GetBedrockAsync(string tenantId) {
        var credentials = await AssumeRoleForTenantAsync(tenantId);
        return new AmazonBedrockClient(credentials);
    }
}
