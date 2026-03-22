# Agent Workspace

This directory is mounted at `/workspace` inside the **agent-terminal** container (Container 2).

Place any files you want the AI agent to read or edit here.

## Usage

```bash
# 1. Start Container 1 (API server)
docker compose up app -d

# 2. Launch the interactive agent terminal (Container 2)
docker compose run --rm agent-terminal

# 3. Choose your agent from the menu:
#    1) Aider    — coding agent
#    2) OpenCode — modern terminal agent
#    3) Shell    — bash shell

# Inside Aider, add files for the agent to work on:
# /add myfile.py
# Then type your task: "What is 1 + 1?"
```

## Tips

- Copy your project files here before starting the agent
- Aider will read and edit files in this directory
- Changes made by the agent are reflected on your host machine
