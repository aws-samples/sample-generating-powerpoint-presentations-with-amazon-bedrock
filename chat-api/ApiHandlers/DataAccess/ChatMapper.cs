using Amazon.DynamoDBv2.Model;
using ApiHandlers.Models;

namespace ApiHandlers.DataAccess
{
    public class ChatMapper
    {
        public static string PK = "sessionId";
        public static string TenantId = "tenantId";
        public static string UserId = "userId";
        public static string Prompts = "prompts";

        public static string Timestamp = "timestamp";
        public static string KnowledgebaseId = "knowledgebaseId";
        public static string Text = "text";
        public static string SessionStatus = "sessionStatus";
        public static string Response = "response";
        
        public static Chat FromDynamoDB(Dictionary<String, AttributeValue> items) {
            var prompts = items.ContainsKey(Prompts) ? items[Prompts].L : [];
            var list = new List<Prompt>();
            foreach(var prompt in prompts) 
            {
                list.Add(ChatPromptsFromDynamoDB(prompt.M));
            }
            var chat = new Chat(items[TenantId].S, items[PK].S, items[SessionStatus].S, items[UserId].S, list.ToArray() );

            return chat;
        }

        internal static Prompt ChatPromptsFromDynamoDB(Dictionary<String, AttributeValue> items)
        {
            return new Prompt(items[Text].S, items[Response].S, long.Parse(items[Timestamp].N));
        }
        
        public static Dictionary<String, AttributeValue> ToDynamoDb(Chat chat) {
            Dictionary<String, AttributeValue> item = new Dictionary<string, AttributeValue>(3);
            item.Add(PK, new AttributeValue(chat.SessionId));
            item.Add(SessionStatus, new AttributeValue(chat.SessionStatus));
            item.Add(UserId, new AttributeValue(chat.UserId));
            item.Add(TenantId, new AttributeValue(chat.TenantId));
            if (chat.Prompts != null && chat.Prompts.Length > 0)
            {
                item.Add(Prompts, ChatPromptsToDynamoDb(chat.Prompts));
            }

            return item;
        }

        internal static AttributeValue ChatPromptsToDynamoDb(Prompt[] prompts)
        {
            var list = new AttributeValue();
            list.L = new List<AttributeValue>();
            if (prompts != null) {
            foreach(var item in prompts)
            {
                var m = new AttributeValue();
                m.M = new Dictionary<String, AttributeValue>();
                list.L.Add(m);
                m.M.Add(Text, new AttributeValue(item.Text));
                m.M.Add(Response, new AttributeValue(item.Response));
                var t = new AttributeValue();
                t.N = item.Timestamp.ToUnixTimeSeconds().ToString();
                m.M.Add(Timestamp, t);
                // m.M[Timestamp].N = item.Timestamp.ToUnixTimeSeconds().ToString();
            }
            }

            return list;
        }
    }
}