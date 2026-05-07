using Amazon.DynamoDBv2;
using Amazon.DynamoDBv2.Model;
using Microsoft.Extensions.Logging;
using ApiHandlers.Models;

namespace ApiHandlers.DataAccess
{
    public class ChatDAO : IDAO
    {
        private static string CHAT_TABLE_NAME = Environment.GetEnvironmentVariable("CHAT_TABLE_NAME") ?? "NA";
        private readonly AmazonDynamoDBClient _dynamoDbClient;
        private readonly ILogger<ChatDAO> _logger;

        public ChatDAO(ILogger<ChatDAO> logger)
        {
            _dynamoDbClient = new AmazonDynamoDBClient();
            _logger = logger;
        }

        public async Task<Chat?> Get(string sessionId)
        {
            _logger.LogDebug("Getting chat for session {0}", sessionId);
            var getItemResponse = await this._dynamoDbClient.GetItemAsync(new GetItemRequest(CHAT_TABLE_NAME,
                new Dictionary<string, AttributeValue>(1)
                {
                    {ChatMapper.PK, new AttributeValue(sessionId)}
                }));
            _logger.LogDebug("Got chat for session {sessionId} {statusCode} {isSet}", sessionId, getItemResponse.HttpStatusCode, getItemResponse.IsItemSet);
            
            return getItemResponse.IsItemSet ? ChatMapper.FromDynamoDB(getItemResponse.Item) : null;
        }

        public async Task Put(string sessionId, Chat chat)
        {
            await this._dynamoDbClient.PutItemAsync(CHAT_TABLE_NAME, ChatMapper.ToDynamoDb(chat));
        }

        public async Task Post(Chat chat)
        {
            await this._dynamoDbClient.PutItemAsync(CHAT_TABLE_NAME, ChatMapper.ToDynamoDb(chat));
        }

        public async Task Delete(string sessionId)
        {
            await this._dynamoDbClient.DeleteItemAsync(CHAT_TABLE_NAME, new Dictionary<string, AttributeValue>(1)
            {
                {ChatMapper.PK, new AttributeValue(sessionId)}
            });
        }
    }
}