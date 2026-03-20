import openai

BASE_URL = "http://localhost:8000/v1"
client = openai.OpenAI(base_url=BASE_URL, api_key="not-needed")

def test_list_models():
    print("=== Testing /v1/models ===")
    models = client.models.list()
    for m in models.data:
        print(f"  - {m.id}")
    print()

def test_chat_completion():
    print("=== Testing /v1/chat/completions (non-streaming) ===")
    response = client.chat.completions.create(
        model="copilot",
        messages=[{"role": "user", "content": "Hello! What time is it right now?"}],
    )
    print(f"Response: {response.choices[0].message.content}")
    print()

def test_chat_completion_streaming():
    print("=== Testing /v1/chat/completions (streaming) ===")
    stream = client.chat.completions.create(
        model="copilot",
        messages=[{"role": "user", "content": "Tell me a short joke."}],
        stream=True,
    )
    print("Streaming: ", end="")
    for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print("\n")

if __name__ == "__main__":
    test_list_models()
    test_chat_completion()
    test_chat_completion_streaming()
    print("All tests completed!")