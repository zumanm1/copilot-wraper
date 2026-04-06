"""
AI Agent that uses the OpenAI-compatible Copilot wrapper to perform local tasks.
Demonstrates tool-use capabilities: time, weather, directory listing, code execution.
"""
import openai
import json
import os
import subprocess
from datetime import datetime
import requests

BASE_URL = "http://localhost:8000/v1"
client = openai.OpenAI(base_url=BASE_URL, api_key="not-needed")

SYSTEM_PROMPT = """You are an AI agent that can help users by performing various tasks.
You have access to tools that you can call to gather information or perform actions.
Available tools:
- get_current_time() -> Returns the current time
- get_weather(city: str) -> Returns weather for a US city
- list_directory(path: str) -> Lists files in a directory
- run_python_code(code: str) -> Executes Python code and returns output
- read_file(path: str) -> Reads a file and returns its contents

When you need to use a tool, respond with a JSON object:
{"tool": "tool_name", "args": {"arg1": "value1"}}

After getting the result, continue your response based on the tool result.
"""

def get_current_time():
    """Get the current time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_weather(city):
    """Get weather for a US city using wttr.in."""
    try:
        url = f"https://wttr.in/{city}?format=j1"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            current = data["current_condition"][0]
            return f"Weather in {city}: {current['weatherDesc'][0]['value']}, {current['temp_C']}°C ({current['temp_F']}°F), Humidity: {current['humidity']}%"
        return f"Could not get weather for {city}"
    except Exception as e:
        return f"Error getting weather: {str(e)}"

def list_directory(path):
    """List files in a directory."""
    try:
        if not os.path.exists(path):
            return f"Path does not exist: {path}"
        files = os.listdir(path)
        if not files:
            return f"Directory is empty: {path}"
        return "\n".join([f"  {f}" for f in files[:20]])  # Limit to 20 files
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def run_python_code(code):
    """Execute Python code and return output."""
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.stdout:
            return result.stdout
        if result.stderr:
            return f"Error: {result.stderr}"
        return "Code executed with no output"
    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out"
    except Exception as e:
        return f"Error executing code: {str(e)}"

def read_file(path):
    """Read and return file contents."""
    try:
        with open(path, 'r') as f:
            return f.read()[:2000]  # Limit to 2000 chars
    except Exception as e:
        return f"Error reading file: {str(e)}"

TOOLS = {
    "get_current_time": get_current_time,
    "get_weather": get_weather,
    "list_directory": list_directory,
    "run_python_code": run_python_code,
    "read_file": read_file,
}

def call_tool(tool_name, args):
    """Call a tool by name with args."""
    if tool_name not in TOOLS:
        return f"Unknown tool: {tool_name}"
    try:
        return TOOLS[tool_name](**args)
    except Exception as e:
        return f"Error calling tool: {str(e)}"

def agent_chat(user_message):
    """Run an agent conversation with the Copilot wrapper."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]
    
    print(f"\n{'='*60}")
    print(f"User: {user_message}")
    print(f"{'='*60}\n")
    
    # First, get the model's response
    response = client.chat.completions.create(
        model="copilot",
        messages=messages,
    )
    
    assistant_msg = response.choices[0].message.content
    print(f"Assistant: {assistant_msg}")
    
    # Check if the model wants to use a tool
    if assistant_msg.strip().startswith("{"):
        try:
            tool_call = json.loads(assistant_msg)
            if "tool" in tool_call:
                tool_name = tool_call["tool"]
                args = tool_call.get("args", {})
                print(f"\n[Calling tool: {tool_name} with args: {args}]")
                result = call_tool(tool_name, args)
                print(f"[Tool result]: {result}")
                
                # Add assistant's tool call and result to messages
                messages.append({"role": "assistant", "content": assistant_msg})
                messages.append({"role": "system", "content": f"Tool result: {result}"})
                
                # Get final response
                response = client.chat.completions.create(
                    model="copilot",
                    messages=messages,
                )
                final_msg = response.choices[0].message.content
                print(f"\nAssistant (after tool): {final_msg}")
                return final_msg
        except json.JSONDecodeError:
            pass
    
    return assistant_msg

def demo_tasks():
    """Run a series of demo tasks."""
    print("\n" + "="*70)
    print("COPILOT AI AGENT DEMO - Tool Use Capabilities")
    print("="*70)
    
    # Task 1: Check the time
    agent_chat("What time is it right now? Use the get_current_time tool.")
    
    # Task 2: Check weather
    agent_chat("What's the weather like in New York? Use the get_weather tool.")
    
    # Task 3: List directory
    agent_chat("List the files in the current directory using list_directory. Use path '.'")
    
    # Task 4: Run Python code
    agent_chat("""Run this Python code using run_python_code:
result = sum(range(1, 101))
print(f"Sum of 1-100: {result}")""")
    
    # Task 5: Create a calculator
    agent_chat("""Generate a simple Python calculator that can add, subtract, multiply, and divide two numbers.
The calculator should be saved to /tmp/calculator.py and then executed with the run_python_code tool.
Make sure to run it with some test calculations.
Here is the code to execute:
calculator_code = '''
def calculator(a, op, b):
    if op == "+": return a + b
    elif op == "-": return a - b
    elif op == "*": return a * b
    elif op == "/": return a / b if b != 0 else "Error: division by zero"

# Test the calculator
print("Calculator Test Results:")
print(f"10 + 5 = {calculator(10, '+', 5)}")
print(f"10 - 5 = {calculator(10, '-', 5)}")
print(f"10 * 5 = {calculator(10, '*', 5)}")
print(f"10 / 5 = {calculator(10, '/', 5)}")
'''
exec(calculator_code)
""")

if __name__ == "__main__":
    demo_tasks()