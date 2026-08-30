import os
from dotenv import load_dotenv
from pinecone import Pinecone

load_dotenv()

api_key = os.getenv("PINECONE_API_KEY")
index_host = os.getenv("PINECONE_INDEX_HOST")

if not api_key:
    raise RuntimeError("PINECONE_API_KEY is missing")

if not index_host:
    raise RuntimeError("PINECONE_INDEX_HOST is missing")

pc = Pinecone(api_key=api_key)
index = pc.Index(host=index_host)

# Save a sample memory
index.upsert_records(
    namespace="__default__",
    records=[
        {
            "_id": "test-memory-1",
            "text": "User likes budget travel and prefers 3-day trips.",
            "memory_type": "preference",
            "user_message": "I like budget trips",
        }
    ],
)

print("Memory saved.")

# Search similar memory
result = index.search(
    namespace="__default__",
    query={
        "inputs": {"text": "budget travel preference"},
        "top_k": 3,
    },
    fields=["text", "memory_type", "user_message"],
)

print("Search result:")
print(result)