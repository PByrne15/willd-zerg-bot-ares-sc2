### Repo purpose

This is a repo containing the code for a custom StarCraft II bot. The bot is built using the ares framework, which is included in the repo in the ares-sc2 directory. Changes to this bot's behaviour should be contained only within this repo, not within the ares-sc2 sub-directory, but implementations from within the ares framework can be used to develop behaviour in this bot.

### Python environment

There is expected to be a virtualenv located at ../.venv that contains everything needed for running poetry against the Python code in the repo. Installing additional things should not be necessary - if anything is needed then ask a human to do it rather than installing things yourself.

### Code structure

The entrypoint for the bot is bot/main.py but most of the behaviour is managed with the controllers in bot/controllers. These controllers share data through a common interface in controllers/controller_data.py so only data that needs to be shared is exposed from the individual controllers.

There are no tests for this repo and there is no need to add any when adding new functionality.

### Framework specifics

Unit behaviour is generally controlled by obtaining all units with a specific role and giving them common (or similar) orders. Trying to change the behaviour for a unit or group of units without changing the role is likely to fail.

There is a useful function called can_win_fight that can be used to get an idea of whether engaging in combat is a good idea but it can be expensive so shouldn't be overused.