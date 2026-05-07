using Amazon.Lambda.Core;
using Amazon.S3;

using Amazon.S3.Model;

namespace AgentProxy
{
    public static class S3Helper
    {
        public static async Task<string> UploadAndReturnPresignedUrl(ILambdaLogger logger, string local_file_path, string tenant_id)
        {
            // Amazon.AWSConfigsS3.UseSignatureVersion4 = true;
            var client = new AmazonS3Client();
            
            var keyPath = string.Format("{0}/output/{1}",tenant_id, Path.GetFileName(local_file_path));
            var bucketName = Environment.GetEnvironmentVariable("S3_BUCKET");
            var response = await client.PutObjectAsync(new Amazon.S3.Model.PutObjectRequest
            {
                BucketName = bucketName,
                Key = keyPath,
                FilePath = local_file_path
            });

            if (response.HttpStatusCode == System.Net.HttpStatusCode.OK)
            {
                var urlRequest = new Amazon.S3.Model.GetPreSignedUrlRequest
                {
                    // ServerSideEncryptionMethod = ServerSideEncryptionMethod.AWSKMS,
                    BucketName = bucketName,
                    Key = keyPath,
                    Expires = DateTime.UtcNow.AddMinutes(5),
                    Verb = HttpVerb.GET
                };

                var url = client.GetPreSignedURL(urlRequest);
                return url;
            }
            else
            {
                throw new Exception("Failed to upload file to S3");
            }
        }
    }
}