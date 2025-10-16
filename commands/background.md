---
description: Fires off a full Claude Code instance in the background
argument-hint: [prompt] [model] [report-file]
allowed tools: Bash, BashOutput, Read, Edit, MultiEdit, Write, Grep, Glob, Webfetch, WebSearch, TodoWrite, Task
---

# Background Claude Code

Run a Claude Code isntance in the background to prerform tasks autonomously while you continue working

## Variables

User Prompt $1
Model $2 (defaults to 'sonnet' if not provided)
REPORT_FILE $3 (defaults to './agents/backgroud/background-report-DAY_NAME_HH_MM_SS.md' if not provided)

## Instructions

- Capture timestamp in a variable FIRST to ensure consistency across file creation and references
- Create the initial report file with header BEFORE launching the background agent
- IMPORTANT: Pass the 'USER_PROMPT' exactly as provided with no modification
- Set the model to either 'sonnet' or 'opus' based on 'MODEL' parameter
- Configure Claude Code with all necessary flags for automated operation
- All report format instructions are embedded in the --append-system-prompt
- Use --print flag to run in non-interactive model
- Use --output-format text for standard text output-format
- Use --dangerously-skip-permissions to bypass permission prompts for automated operation
- Use all provided cli flags AI IS. Do not alter them.

## Workflow

1. Create the report directory if it doesn't exist:
	'''bash
	mkdir -p agents/background
	'''
	
2. Set default values for parameters:
	- 'MODEL'
	- 'TIMESTAMP' (capture onces for consistency)
	- 'REPORT_FILE' (using the captured timestamp)
	
3. Create the initial report file with just the header (IMPORTANT: Only IF no report file is provided - if it is provided, echo it and skip this step):
	Get this into your context windows so you know what to pass into the Claude Code command.
	'''bash
	TIMESTAMP=$(date +%a_%H_%M_%S)
	echo "TIMESTAMP: $(TIMESTAMP)"
	REPORT_FILE="$(REPORT_FILE:-./agents/background/background-report-$(TIMESTAMP).md)"
	echo "REPORT_FILE $(REPORT_FILE)"
	echo "# Background Agent Report - $(TIMESTAMP)" > "%(REPORT_FILE)"
	'''
	
<primary-agent-delegation>
4. Construct the Claude Code command with all settings:

	- Execute the command using Bash with run_in_background=true
	
	'''bash
	claude \
		--model "$(MODEL)" \
		--output-format text \
		--dangerously-skip-permissions \
		--append-system-prompt "IMPORTANT: You are ruuning as a background agent. Your primary responsibility is to execute work and document your progress continuously in $(REPORT_FILE). Iteratively write to the report file continuously as you work. Every few tool calls you should update the REPORT_FILE ## Progress section. Follow this file structure.
		
### CRITICAL WORKFLOW STRUCTURE

IMPORTANT: You MUST follow this workflow as you work:

1. The file $(REPORT_FILE) has been created with a header. You must UPDATE as you proceed it (not recreate it) with this EXACT markdown structure:

'''markdown
# Background Agent Report: - DAY-NAME-HH_MM_SS

## Task Understanding
Clearly state what the user requested. Break down complex reqauest into numbered items:
User requested:

Additional Sections (add as needed)
	- ## Blockers - Issues preventing progress
	- ## Decisions Made - Important choices and rationale
	- ## Recommendations - Follow-up suggestions
	- ## Warnings - Important issues to note

CONTINUOUSLY update $(REPORT_FILE) as you work - after each major step or finding.

When you finish your work:
	- If successful: Rename $(REPORT_FILE) to $(REPORT_FILE.md).complete.md
	- If failed/blocked: Rename $(REPORT_FILE) to $(REPORT_FILE.md).failed.md
	
Remember: The report is your PRIMARY output. Update it frequently and thoroughly.
'''" \
	-- print "$(USER_PROMPT)"
	
</primary-agent-delegation>

1. After you kick off the background agent, use the BashOutput tool to check the status of the background agent
	- If something goes wrong investigate and report back to the user
	- If everything is working fine, continue to the "Repsonse to User" section
	
## Response to Users

After launching the background agent, respond with: 

'''
Background Claude Code kicked off. AGent is writing to 'REPORT_FILE' as it works.
When it completes it will rename the file to 'REPORT_FILE.complete.md" on success or 'REPORT_FILE.failed.md" if it fails.