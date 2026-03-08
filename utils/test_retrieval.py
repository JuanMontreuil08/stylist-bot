import boto3
import os
from dotenv import load_dotenv

load_dotenv()

bedrock_agent = boto3.client('bedrock-agent-runtime', region_name='us-east-1')

KB_ID = os.getenv("KNOWLEDGE_BASE_ID")

def query_with_rerank(query):
    response = bedrock_agent.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={'text': query},
        retrievalConfiguration={
            'vectorSearchConfiguration': {
                'numberOfResults': 10,
                'overrideSearchType': 'HYBRID',
                'rerankingConfiguration': {
                    'type': 'BEDROCK_RERANKING_MODEL',
                    'bedrockRerankingConfiguration': {
                        'modelConfiguration': {
                            'modelArn': 'arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0'
                        },
                        'numberOfRerankedResults': 2
                    }
                }
            }
        }
    )
    
    print(f"Total: {len(response['retrievalResults'])}\n")
    for item in response['retrievalResults']:
        print(f"📊 Score: {item['score']:.3f}")
        print(f"🖼️  {item['location']['s3Location']['uri']}")
        print(f"📝 Metadata:")
        for k, v in item['metadata'].items():
            if k != 's3_url':  # Ignore s3_url
                print(f"   {k}: {v}")
        print("=" * 80)

query_with_rerank("trajes elegantes de colores llamativos para una fiesta")



