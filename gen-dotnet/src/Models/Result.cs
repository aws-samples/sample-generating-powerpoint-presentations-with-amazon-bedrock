namespace AgentProxy.Models
{
    public class Result
    {
        public string? Message { get; set; }

        public Result(string? message)
        {
            Message = message;
        }
    }
}