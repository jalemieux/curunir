# run.py
import asyncio

from dotenv import load_dotenv

from src.agent.agent import Agent
from src.config import AgentConfig


async def main():
    load_dotenv()
    config = AgentConfig()
    agent = Agent(config)

    print("Curunir agent ready. Type 'quit' to exit.\n")
    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() == "quit":
            break
        response = await agent.handle(user_input, session_id="cli")
        print(f"\n{response}\n")


if __name__ == "__main__":
    asyncio.run(main())
