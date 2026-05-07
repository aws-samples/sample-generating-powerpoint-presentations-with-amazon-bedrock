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
            // services.AddAWSSDK<IAmazonDynamoDB>();
            // services.AddAWSService<AmazonDynamoDBClient>();
            // // services.AddAWSService<AmazonSecurityTokenServiceClient>();
            // services.AddAWSService<AmazonBedrockAgentClient>();
            // services.AddAWSService<AmazonBedrockAgentRuntimeClient>();
            // services.AddAWSService<AmazonBedrockRuntimeClient>();
            services.AddScoped<ChatDAO>();
            services.AddScoped<ChatApiHandler>();
            services.AddScoped<AWSClientFactory>();
            services.AddHttpContextAccessor();
            services.AddOptions<RedrockAgentConfig>().Configure((config) => {
               config.AgentAliasId = System.Environment.GetEnvironmentVariable("AGENT_ALIAS_ID");
               config.AgentId = System.Environment.GetEnvironmentVariable("AGENT_ID");
            });

            services.AddAWSLambdaHosting(LambdaEventSource.RestApi);
            
            return services;
        }
    }
}