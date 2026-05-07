using System;
using System.Collections.Generic;
using System.Net;
using System.Text.Json;
using System.Threading.Tasks;
using ApiBootstrap;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using ApiHandlers;
using ApiHandlers.DataAccess;
using ApiHandlers.Models;
using Microsoft.Extensions.Configuration;

var builder = WebApplication.CreateSlimBuilder(args);
builder.Configuration.AddEnvironmentVariables();
builder.Services.AddServices(builder.Configuration);
builder.Logging.ClearProviders();
builder.Logging.AddJsonConsole(options =>
{
    options.IncludeScopes = true;
    options.UseUtcTimestamp = true;
    options.TimestampFormat = "hh:mm:ss ";
    
});

var app = builder.Build();

// var chatDao = app.Services.GetRequiredService<ChatDAO>();
var handlers = app.Services.GetRequiredService<ChatApiHandler>();
Amazon.Lambda.Core.SnapshotRestore.RegisterBeforeSnapshot(async () => await BeforeCheckpoint(app.Logger, handlers));
Amazon.Lambda.Core.SnapshotRestore.RegisterBeforeSnapshot(AfterRestore);

app.MapGroup("/v1/chat").MapChatApi(handlers);
// app.MapGet("/v1/chat/{sessionId}", async (string sessionId) => await handlers.Get(sessionId));


app.Run();

static async ValueTask BeforeCheckpoint(ILogger logger, ChatApiHandler handlers)
{
    logger.LogInformation("Before checkpoint");
    
    for (int i = 0; i < 5; i++)
    {
        await handlers.GetSession("something", "something");
    }
    
    logger.LogInformation("Before checkpoint");
}

static ValueTask AfterRestore()
{
    Console.WriteLine("After restore");
    
    return ValueTask.CompletedTask;
}