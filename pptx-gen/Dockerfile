# The python dependency `unoconv` uses `distutils` from python standard library
# which was deprecated in Python 3.12
FROM public.ecr.aws/lambda/python:3.11 

# Copy requirements.txt
COPY requirements.txt ${LAMBDA_TASK_ROOT}
# Copy main lambda script
COPY pptx-generator-lambda.py ${LAMBDA_TASK_ROOT}
# Copy python module
COPY src/ ${LAMBDA_TASK_ROOT}/src/
# Copy temp files
COPY tmp/ ${LAMBDA_TASK_ROOT}/tmp/
COPY templates/ ${LAMBDA_TASK_ROOT}/templates/

# Install the required python packages
RUN pip install -r requirements.txt

USER 1001

HEALTHCHECK NONE

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "pptx-generator-lambda.handler" ]