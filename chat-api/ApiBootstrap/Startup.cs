using Amazon.DynamoDBv2;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using ApiHandlers;
using ApiHandlers.DataAccess;
using Amazon.BedrockRuntime;
using Amazon.SecurityToken;
using ApiHandlers.Options;
using Amazon.BedrockAgentRuntime;
using Amazon.BedrockAgent;

namespace ApiBootstrap
{
    public static class Startup
    {
        public static IServiceCollection AddServices(this IServiceCollection services, IConfiguration configuration)
        {
            services.AddDefaultAWSOptions(configuration.GetAWSOptions());
            services.AddScoped<ChatDAO>();
            services.AddScoped<ChatApiHandler>();
            services.AddScoped<AWSClientFactory>();
            services.AddHttpContextAccessor();
            services.AddOptions<RedrockAgentConfig>().Configure((config) => {
               config.AgentRuntimeEndpointArn = System.Environment.GetEnvironmentVariable("AGENT_RUNTIME_ENDPOINT_ARN") ?? "";
            });

            services.AddAWSLambdaHosting(LambdaEventSource.RestApi);
            
            return services;
        }
    }
}
