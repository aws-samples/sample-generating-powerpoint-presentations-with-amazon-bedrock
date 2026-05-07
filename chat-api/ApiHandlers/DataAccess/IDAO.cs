using ApiHandlers.Models;

namespace ApiHandlers.DataAccess
{
    public interface IDAO
    {        
        Task<Chat?> Get(string sessionId);

        Task Put(string sessionId, Chat chat);

        Task Post(Chat chat);

        Task Delete(string sessionId);
    }
}