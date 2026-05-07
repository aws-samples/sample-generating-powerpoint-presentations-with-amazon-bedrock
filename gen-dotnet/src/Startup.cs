using Amazon.Lambda.Annotations;
using Amazon.Lambda.Core;
using Amazon.Runtime.Internal.Util;

// Assembly attribute to enable the Lambda function's JSON input to be converted into a .NET class.
[assembly: LambdaSerializer(typeof(Amazon.Lambda.Serialization.SystemTextJson.DefaultLambdaJsonSerializer))]

namespace AgentProxy;