using System;
using System.Text;
using System.Diagnostics.CodeAnalysis;

namespace ApiHandlers.Models
{
    public class Chat
    {
        [SetsRequiredMembers]
        public Chat(string tenantId, string sessionId, string sessionStatus, string userId, Prompt[] prompts)
        {
            this.TenantId = tenantId;
            this.SessionId = sessionId;
            this.SessionStatus = sessionStatus;
            this.UserId = userId;
            this.Prompts = prompts;
        }
        public required string TenantId { get; set; }        
        public required string SessionId { get; set; }
        public required string SessionStatus{ get; set; }
        public required string UserId { get; set; }        
        public Prompt[] Prompts { get; set; }

        public override string ToString()
        {
            return "ChatSession{" +
                   "SessionId='" + this.SessionId + '\'' +
                   ", TenantId='" + this.TenantId + '\'' +
                   ", UserId='" + this.UserId + '\'' +
                   ", SessionStatus='" + this.SessionStatus + '\'' +
                   ", Prompts='" + this.Prompts.ToString() + '\'' +
                   '}';
        }
    }

    public class Prompt
    {
        public required string Text { get; set; }
        public string Response { get; set; } = "";
        public DateTimeOffset Timestamp { get; set; } = DateTimeOffset.UtcNow;
        public Prompt() {}

        [SetsRequiredMembers]
        public Prompt(string text, string response, long timestamp)
        {
            this.Text = text;
            this.Response = response;
            this.Timestamp = DateTimeOffset.FromUnixTimeSeconds(timestamp);
        }

        public override string ToString()
        {
            return "Prompt{" +
                   "Text='" + this.Text + '\'' +
                   "Response='" + this.Response + '\'' +
                   ", Timestamp=" + this.Timestamp.ToUnixTimeSeconds() +
                   '}';
        }
    }

    public static class PromptExtension
    {
        public static string ToString(this Prompt[] prompts)
        {
            var strBuilder = new StringBuilder();
            strBuilder.Append("[");
            foreach(var item in prompts)
            {
                strBuilder.Append(item.ToString() + ", ");
            }
            strBuilder.Append("]");

            return strBuilder.ToString();
        }
    }
}