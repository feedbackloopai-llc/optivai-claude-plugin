Testing Configuration

### Container Status & Rebuild
```bash
# Services: frontend(:80,:3000), backend(:8000), localstack(:4566), redis(:6379), minio(:9000-9001), opensearch(:9200,9600)
docker-compose down
docker rmi confluence-mcp-frontend || true
docker-compose up --build -d
docker-compose logs -f frontend backend
```

To Test Login using a validated user
Use the login in login_test.json
This user can generate a JWT

To Test LLM Connections and using a Real API Keys - you can load these into secrets manager for full end to end testing of LLM integration from these providers
ANTHROPIC_API_KEY="your-anthropic-api-key-here"
OPENAI_API_KEY="your-openai-api-key-here"
GOOGLE_API_KEY="your-google-api-key-here"

### Environment Variables
```env
AWS_COGNITO_REGION=us-east-2
AWS_COGNITO_USER_POOL_ID=us-east-2_jZxHuh8pM
AWS_COGNITO_CLIENT_ID=1eg663k23sp46ea38sbqr3rg5m
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost
```

### Authentication Flow
User → AWS Cognito → JWT → API Gateway → Backend → AWS Secrets Manager → API Keys → LLM Providers

### Security Features
- MFA via AWS Cognito, per-user API key isolation, comprehensive audit logging, RBAC, VPC endpoints, encryption at rest/transit
