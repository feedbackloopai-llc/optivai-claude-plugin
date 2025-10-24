---
description: Execute GenSI strategic planning with Autonomous Agentic Pipeline (AAP) architecture - dynamic template-based scaling
---

# GenSI Strategic Planning Command

## Command Help

### Usage

```
/gensi <request-file> <starting-phase> <output-folder>
```

### Parameters (All Required)

**1. request-file** (required)
   - Path to your strategic planning request file
   - Can be any markdown filename and location
   - Examples:
     - `./si-request.md`
     - `fitnessapp-product/newproductrequest.md`
     - `~/projects/hr-system/strategic-plan-req.md`

**2. starting-phase** (required)
   - Phase number to start execution from: `0`, `1`, `2`, `3`, `4`, `5`, or `6`
   - **0** = Start from beginning (Foundation Research)
   - **1** = Skip Phase 0, start from Strategic Initiatives
   - **2** = Skip Phases 0-1, start from Business Model Development
   - **3** = Skip Phases 0-2, start from Solution Design
   - **4** = Skip Phases 0-3, start from Strategic Planning
   - **5** = Skip Phases 0-4, start from Program Planning
   - **6** = Skip Phases 0-5, start from Execution Planning

**3. output-folder** (required)
   - Directory path where all outputs and logs will be created
   - Will be created if doesn't exist
   - Examples:
     - `./output`
     - `fitnessapp-product/gensi-outputs`
     - `~/projects/hr-system/results`

### Request File Format

Your request file must be a markdown file (.md) containing the Essential Inputs defined in `~/.claude/instructions/business-artifact-instructions/strategy/strategic-planning-main-instructions.md`.

**Key Required Fields:**

1. **Organization Profile**
   - Organization name, website, industry, size, geographic presence

2. **Initiative Configuration**
   - **type**: Planning type (determines default initiative count)
     - `new-project` (default) - Single focused project
     - `org-annual` - Annual organizational strategic planning
     - `new-product` - Major product development
     - `new-feature` - Single feature development

   - **tot-initiatives**: Total number of strategic initiatives to create
     - Optional - overrides type default if specified
     - Type defaults:
       - `new-project` → 1 initiative
       - `org-annual` → 5 initiatives
       - `new-product` → 5 initiatives
       - `new-feature` → 1 initiative

3. **Planning Purpose**
   - New Product/Service Development, Strategic Initiatives, or Job Description-based

4. **Documentation Size**
   - Small (2 pages), Medium (5 pages), Large (10 pages), or Full (10+ pages)

5. **Stakeholder Information**
   - Key decision makers, budget, timeline

**See `~/.claude/instructions/business-artifact-instructions/strategy/strategic-planning-main-instructions.md` (section "Essential Inputs") for complete format details.**

### Examples

**Basic usage (start from beginning):**
```
/gensi ./si-request.md 0 ./output
```

**Start from Phase 2 with custom paths:**
```
/gensi fitnessapp-product/newproductrequest.md 2 fitnessapp-product/gensi-outputs
```

**Full path example:**
```
/gensi ~/projects/hr-system/strategic-plan-req.md 0 ~/projects/hr-system/results
```

---

# GenSI Strategic Planning Command - AAP Orchestration Mode

**AGENT EXECUTION MODE: AAP ORCHESTRATION**

You are the MAIN ORCHESTRATION AGENT (GSPM - GenSI Strategic Planning Main). Your role is to:
1. Validate inputs and read configuration
2. Launch Phase 0 subagent (single instance)
3. Launch Phase 1 template N times in parallel (N = initiative count from si-request.md)
4. Launch Phases 2-6 templates N×5 times in SINGLE message (AAP pattern)
5. Validate all subagent outputs
6. Create execution log with real timestamps
7. Report final results to user

**KEY ARCHITECTURAL PATTERN:**
- Phase 0: Single subagent
- Phase 1: Template invoked N times in parallel (N = initiative count)
- Phases 2-6: Templates invoked N×5 times total (AAP with dependency polling)
- Templates scale dynamically - supports ANY number of initiatives

DO NOT execute phase work directly - ONLY orchestrate subagents.

## Parameters Provided

**Parameter 1 (si-request file path):** `{{arg1}}`
**Parameter 2 (Starting Phase):** `{{arg2}}`
**Parameter 3 (Output Folder):** `{{arg3}}`

---

## Execution Protocol

### Step 1: Read Configuration and Initialize

#### 1.1 Read Main Strategic Planning Instructions
- Read: `~/.claude/instructions/business-artifact-instructions/strategy/strategic-planning-main-instructions.md`
- This provides overall orchestration context and AAP architecture overview

#### 1.2 Read si-request.md File and Extract Initiative Configuration
- Read file at path specified in Parameter 1 (`{{arg1}}`)
- Extract:
  - Organization name and size (Small/Medium/Large/XL)
  - **Initiative type** (`type` field): new-project, org-annual, new-product, or new-feature
  - **Initiative count** (`tot-initiatives` field): If specified, use this value. If NOT specified, use type default:
    - `new-project` → 1 initiative (default)
    - `org-annual` → 5 initiatives (default)
    - `new-product` → 5 initiatives (default)
    - `new-feature` → 1 initiative (default)
  - Strategic objectives and requirements

Save the final initiative count as INIT_COUNT for use throughout execution.

**Present Initiative Configuration to User:**

After reading si-request.md, present a clear summary to the user:

```
**Initiative Configuration:**
- Type: [type value from file, e.g., "new-product"]
- Initiative Count: [N] initiatives
  [If user specified tot-initiatives]: (User-specified value)
  [If using default]: (Default for [type])

This means:
- Phase 1 will create [N] strategic initiatives
- Phases 2-4 will scale based on [N] initiatives
- Total expected files: [calculated based on N]
```

**Example output for user-specified:**
```
**Initiative Configuration:**
- Type: new-product
- Initiative Count: 2 initiatives (User-specified value)

This means:
- Phase 1 will create 2 strategic initiatives
- Phases 2-4 will scale based on 2 initiatives
- Total expected files: 28 files (3 + 1 + 2 + 6 + 6 + 10)
```

**Example output for default:**
```
**Initiative Configuration:**
- Type: org-annual
- Initiative Count: 5 initiatives (Default for org-annual planning)

This means:
- Phase 1 will create 5 strategic initiatives
- Phases 2-4 will scale based on 5 initiatives
- Total expected files: 58 files (3 + 1 + 5 + 15 + 15 + 25)
```

#### 1.3 Validate Starting Phase
- Parameter 2 (`{{arg2}}`) must be 0, 1, 2, 3, 4, 5, or 6
- If invalid, inform user and stop

#### 1.4 Create Output and Logs Folders
- Output folder is Parameter 3 (`{{arg3}}`) - REQUIRED
- Create output folder: `mkdir -p {output_folder}`
- Create logs folder: `mkdir -p {output_folder}/logs`
- Save logs folder path as LOG_DIR: `{output_folder}/logs`

#### 1.5 Create Orchestrator Execution Log with Real Timestamps
- Capture current timestamp using bash: `date +"%Y%m%d-%H%M%S"` → save as TIMESTAMP
- Create log file: `{LOG_DIR}/orchestrator-${TIMESTAMP}.md`
- Capture start time using bash: `date +"%Y-%m-%d %H:%M:%S"` → save as START_TIME
- Initialize log file using Write tool with:
  ```markdown
  # GenSI Execution Log
  **Date:** [extract from START_TIME]
  **Time:** [extract from START_TIME]
  **si-request File:** {{arg1}}
  **Starting Phase:** {{arg2}}
  **Output Folder:** {output_folder}
  **Architecture:** Autonomous Agentic Pipeline (AAP) v2.0

  ---

  ## Execution Timeline

  [${START_TIME}] GSPM: GenSI orchestration started
  [${START_TIME}] GSPM: Configuration validated
  ```

**CRITICAL LOGGING PATTERN:**
- Use bash `date +"%Y-%m-%d %H:%M:%S"` to capture real timestamps
- Use Write tool to append log entries (NOT bash cat/echo)
- Example: Capture timestamp → Use Write tool with that timestamp value

---

### Step 2: Validate Prerequisites

**IMPORTANT**: Use Read or Glob tools for all file verification. DO NOT use bash ls commands.

#### 2.1 Verify Agent Template Files Exist

Use Glob tool to verify agent files:
```
Glob pattern: "gensi-phase*.md"
Path: ~/.claude/agents/
```

Required files (verify all exist):
- gensi-phase0-executor.md
- gensi-phase1-initiative-planner.md
- gensi-phase1-initiative-executor.md
- gensi-phase2-initiative-worker.md
- gensi-phase3-initiative-worker.md
- gensi-phase4-initiative-worker.md
- gensi-phase5-initiative-worker.md
- gensi-phase6-initiative-worker.md

If any missing, inform user and stop.

#### 2.2 Verify Phase Instruction Files Exist

Use Glob tool to verify instruction files:
```
Glob pattern: "strategic-planning-phase-*.md"
Path: ~/.claude/instructions/business-artifact-instructions/strategy/
```

Required files (verify all 7 exist):
- strategic-planning-phase-0-instructions.md
- strategic-planning-phase-1-instructions.md
- strategic-planning-phase-2-instructions.md
- strategic-planning-phase-3-instructions.md
- strategic-planning-phase-4-instructions.md
- strategic-planning-phase-5-instructions.md
- strategic-planning-phase-6-instructions.md

#### 2.3 Verify Supporting Files Exist

Use Read tool to verify (attempt to read file - if fails, file doesn't exist):
- `~/.claude/instructions/style-guides/documentation-guidelines.md`

#### 2.4 Verify Prior Phase Outputs (if starting phase > 0)

Use Glob tool to count and verify output files:

- **If starting Phase 1**: Glob for `phase0-step*.md` in output_folder (expect 3 files)
- **If starting Phase 2**: Glob for `phase0-step*.md` and `phase1-step*.md` in output_folder
- **If starting Phase 3**: Glob for `phase0-step*.md`, `phase1-step*.md`, `phase2-*.md` in output_folder
- **If starting Phase 4**: Glob for `phase0-step*.md`, `phase1-step*.md`, `phase2-*.md`, `phase3-*.md` in output_folder
- **If starting Phase 5**: Glob for `phase0-step*.md`, `phase1-step*.md`, `phase2-*.md`, `phase3-*.md`, `phase4-*.md` in output_folder
- **If starting Phase 6**: Glob for `phase0-step*.md`, `phase1-step*.md`, `phase2-*.md`, `phase3-*.md`, `phase4-*.md`, `phase5-*.md` in output_folder

**Dynamic Counting:**
- Count Phase 1 files: Use Glob tool with pattern `phase1-step*.md` in {output_folder} → count results → save as INIT_COUNT
- Expected Phase 2 files: INIT_COUNT × 4
- Expected Phase 3 files: INIT_COUNT × 3
- Expected Phase 4 files: INIT_COUNT × 3
- Expected Phase 5 files: INIT_COUNT × 4
- Expected Phase 6 files: INIT_COUNT × 3

---

### Step 3: Execute Phases Using AAP Pattern

For each phase (starting from Parameter 2 through Phase 4):

---

#### PHASE 0: Single Subagent (if starting phase ≤ 0)

**Launch Phase 0 Executor:**

1. Capture timestamp: `date +"%Y-%m-%d %H:%M:%S"` → P0_LAUNCH_TIME
2. Log to execution log: `[${P0_LAUNCH_TIME}] GSPM: Launching Phase 0 executor`

3. Launch using Task tool:
   ```
   <invoke name="Task">
     <parameter name="description">Execute GenSI Phase 0 - Foundation Research</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase0-executor subagent.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase0-executor.md

   Inputs:
   - si_request_path: {{arg1}}
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}

   Execute all Phase 0 tasks according to the agent instructions.</parameter>
   </invoke>
   ```

4. Wait for completion report
5. Capture completion time: `date +"%Y-%m-%d %H:%M:%S"` → P0_COMPLETE_TIME
6. Log: `[${P0_COMPLETE_TIME}] GSPM: Phase 0 executor completed`

**Validate Phase 0 Outputs:**
- Expected files: 3 (phase0-step1-org-profile.md, phase0-step2-market-research.md, phase0-step3-current-state.md)
- Count using Glob tool: pattern `phase0-step*.md`, path `{output_folder}` → count results
- If count ≠ 3, report failure and handle retry

7. Log: `[timestamp] GSPM: Phase 0 validation passed (3 files created)`

---

#### PHASE 1: Two-Step Execution (if starting phase ≤ 1)

Phase 1 executes in TWO sequential steps:
1. **Step 1**: Single subagent creates initiative plan
2. **Step 2**: N parallel subagents create detailed initiatives

---

#### PHASE 1 STEP 1: Initiative Planning (Single Subagent)

**Purpose**: Create initiative summary plan listing all initiatives

**Launch Phase 1 Step 1 Planner:**

1. Capture timestamp: `date +"%Y-%m-%d %H:%M:%S"` → P1S1_LAUNCH_TIME
2. Log to orchestrator log: `[${P1S1_LAUNCH_TIME}] GSPM: Launching Phase 1 Step 1 initiative planner`

3. Launch using Task tool:
   ```
   <invoke name="Task">
     <parameter name="description">Execute Phase 1 Step 1 - Initiative Planning</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase1-initiative-planner subagent.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase1-initiative-planner.md

   Inputs:
   - si_request_path: {{arg1}}
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}

   Create initiative summary plan according to agent instructions.</parameter>
   </invoke>
   ```

4. Wait for completion report
5. Capture completion time: `date +"%Y-%m-%d %H:%M:%S"` → P1S1_COMPLETE_TIME
6. Log: `[${P1S1_COMPLETE_TIME}] GSPM: Phase 1 Step 1 planner completed`

**Validate Phase 1 Step 1 Output:**
- Expected files: 1 (phase1-step1-initiative-plan.md)
- Verify using Read tool: attempt to read `{output_folder}/phase1-step1-initiative-plan.md`
- If file doesn't exist (Read fails), report failure and handle retry

7. Log: `[timestamp] GSPM: Phase 1 Step 1 validation passed (initiative plan created)`

**Extract Initiative Count from Plan:**

8. Read `{output_folder}/phase1-step1-initiative-plan.md`
9. Search for line containing "**Total Initiatives:**" and extract N
10. Save as INIT_COUNT variable
11. Log: `[timestamp] GSPM: Initiative count extracted from plan: N=${INIT_COUNT}`

---

#### PHASE 1 STEP 2: Initiative Creation (N Parallel Subagents)

**Purpose**: Create comprehensive initiative documents based on plan

**Launch N Phase 1 Step 2 Initiative Executors in SINGLE Message:**

1. Capture timestamp: `date +"%Y-%m-%d %H:%M:%S"` → P1S2_LAUNCH_TIME
2. Log: `[${P1S2_LAUNCH_TIME}] GSPM: Launching ${INIT_COUNT} parallel Phase 1 Step 2 initiative executors`

3. Launch INIT_COUNT instances using Task tool in ONE message:
   ```
   <function_calls>
   <invoke name="Task">
     <parameter name="description">Execute Phase 1 Step 2 Initiative 1</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase1-initiative-executor for INITIATIVE 1.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase1-initiative-executor.md

   Inputs:
   - si_request_path: {{arg1}}
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}
   - initiative_number: 1
   - total_initiatives: ${INIT_COUNT}

   Create Initiative 1 according to agent instructions.</parameter>
   </invoke>
   <invoke name="Task">
     <parameter name="description">Execute Phase 1 Step 2 Initiative 2</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase1-initiative-executor for INITIATIVE 2.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase1-initiative-executor.md

   Inputs:
   - si_request_path: {{arg1}}
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}
   - initiative_number: 2
   - total_initiatives: ${INIT_COUNT}

   Create Initiative 2 according to agent instructions.</parameter>
   </invoke>
   [... repeat for all ${INIT_COUNT} initiatives ...]
   </function_calls>
   ```

**CRITICAL**: Repeat the Task invocation pattern for ALL ${INIT_COUNT} initiatives in the SAME message.

4. Wait for all INIT_COUNT subagent completions
5. Capture completion time: `date +"%Y-%m-%d %H:%M:%S"` → P1S2_COMPLETE_TIME
6. Log: `[${P1S2_COMPLETE_TIME}] GSPM: All ${INIT_COUNT} Phase 1 Step 2 initiative executors completed`

**Validate Phase 1 Step 2 Outputs:**
- Expected files: INIT_COUNT (phase1-step2-initiative1.md through phase1-step2-initiative${INIT_COUNT}.md)
- Count using Glob tool: pattern `phase1-step2-initiative*.md`, path `{output_folder}` → count results
- If count ≠ INIT_COUNT, report failure and handle retry

7. Log: `[timestamp] GSPM: Phase 1 Step 2 validation passed (${INIT_COUNT} initiatives created)`

**Phase 1 Complete:**
- Total files created: 1 + INIT_COUNT
- Step 1: initiative plan
- Step 2: INIT_COUNT detailed initiatives

---

#### PHASES 2-6: Staggered Launch with Phase Gates

**ARCHITECTURE CHANGE (v4.0.1 - Bug Fix):**
- Phases launch SEQUENTIALLY (not simultaneously)
- Each phase: Launch workers → Poll for completion → Validate → Launch next phase
- Orchestrator monitors completion (no worker timeout failures)
- Trade-off: ~60-90 min slower, but 100% reliable
- Workers retain polling as safety net (60-second timeout)

**Pattern Per Phase:**
1. Launch N workers for phase
2. Poll every 30 seconds until all files created
3. Validate file count matches expected
4. Report progress to user
5. Launch next phase

---

#### PHASE 2: Business Models Launch

**Step 2.1: Launch Phase 2 Workers**

1. Capture timestamp: `date +"%Y-%m-%d %H:%M:%S"` → PHASE2_LAUNCH_TIME
2. Calculate expected files: EXPECTED_P2 = INIT_COUNT × 4
3. Log: `[${PHASE2_LAUNCH_TIME}] GSPM: Launching Phase 2 workers (${INIT_COUNT} initiatives, ${EXPECTED_P2} files expected)`
4. Report to user: "Launching Phase 2: Business Models (${INIT_COUNT} initiatives × 4 files = ${EXPECTED_P2} expected)..."

5. Launch N Phase 2 workers in SINGLE message:
   ```
   <function_calls>
   <!-- Phase 2 workers: N instances -->
   <invoke name="Task">
     <parameter name="description">Execute Phase 2 for Initiative 1</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase2-initiative-worker for INITIATIVE 1.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase2-initiative-worker.md

   Inputs:
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}
   - initiative_number: 1
   - total_initiatives: ${INIT_COUNT}

   Process Initiative 1 according to agent instructions.</parameter>
   </invoke>
   [... repeat for initiatives 2 through INIT_COUNT with incrementing initiative_number ...]
   </function_calls>
   ```

**Step 2.2: Poll for Phase 2 Completion**

Execute bash polling script:

```bash
OUTPUT_DIR="{output_folder}"
INIT_COUNT=${INIT_COUNT}
EXPECTED_FILES=$((INIT_COUNT * 4))
START_TIME=$(date +%s)
TIMEOUT=3600  # 60 minutes
POLL_INTERVAL=30
LAST_REPORT=0

echo "GSPM: Waiting for Phase 2 completion (${EXPECTED_FILES} files expected)..."

while true; do
  COUNT=$(ls "${OUTPUT_DIR}"/phase2-step*.md 2>/dev/null | wc -l | tr -d ' ')

  if [ "$COUNT" -eq $EXPECTED_FILES ]; then
    WAIT_TIME=$(date +%s)
    TOTAL_WAIT=$((WAIT_TIME - START_TIME))
    echo "GSPM: Phase 2 complete (${COUNT} files in ${TOTAL_WAIT}s)"
    break
  fi

  CURRENT_TIME=$(date +%s)
  ELAPSED=$((CURRENT_TIME - START_TIME))

  if [ "$ELAPSED" -gt "$TIMEOUT" ]; then
    echo "ERROR: Phase 2 timeout - found ${COUNT}/${EXPECTED_FILES} files after ${ELAPSED}s"
    echo "Check logs in {LOG_DIR}/phase2-init*.md for worker errors"
    exit 1
  fi

  # Report progress every 2 minutes
  if [ $((ELAPSED - LAST_REPORT)) -ge 120 ]; then
    echo "Phase 2: ${COUNT}/${EXPECTED_FILES} files (${ELAPSED}s elapsed)"
    LAST_REPORT=$ELAPSED
  fi

  sleep $POLL_INTERVAL
done
```

**Step 2.3: Validate Phase 2 Output**

1. Count files using Glob tool: pattern `phase2-step*.md`, path `{output_folder}` → P2_COUNT
2. Expected: INIT_COUNT × 4
3. If P2_COUNT ≠ (INIT_COUNT × 4):
   - Log: `[timestamp] ERROR: Phase 2 validation failed (found ${P2_COUNT}, expected ${INIT_COUNT * 4})`
   - HALT execution (do NOT launch Phase 3)
   - Report to user: "Phase 2 validation failed - check logs"
   - Exit with error
4. Log: `[timestamp] GSPM: Phase 2 validation passed (${P2_COUNT} files)`
5. Report to user: "✓ Phase 2: COMPLETE (${P2_COUNT} files)"

---

#### PHASE 3: Solution Design Launch

**Step 3.1: Launch Phase 3 Workers**

1. Capture timestamp: `date +"%Y-%m-%d %H:%M:%S"` → PHASE3_LAUNCH_TIME
2. Calculate expected files: EXPECTED_P3 = INIT_COUNT × 3
3. Log: `[${PHASE3_LAUNCH_TIME}] GSPM: Launching Phase 3 workers (${INIT_COUNT} initiatives, ${EXPECTED_P3} files expected)`
4. Report to user: "Launching Phase 3: Solution Design (${INIT_COUNT} initiatives × 3 files = ${EXPECTED_P3} expected)..."

5. Launch N Phase 3 workers in SINGLE message:
   ```
   <function_calls>
   <!-- Phase 3 workers: N instances -->
   <invoke name="Task">
     <parameter name="description">Execute Phase 3 for Initiative 1</parameter>
     <parameter name="subagent_type">worker</parameter>
     <parameter name="prompt">You are executing gensi-phase3-initiative-worker for INITIATIVE 1.

   Read and follow ALL instructions in: ~/.claude/agents/gensi-phase3-initiative-worker.md

   NOTE: Phase 2 dependencies guaranteed to exist (orchestrator validated). Worker polling is safety net only.

   Inputs:
   - output_dir: {output_folder}
   - log_dir: {LOG_DIR}
   - initiative_number: 1
   - total_initiatives: ${INIT_COUNT}

   Process Initiative 1 according to agent instructions.</parameter>
   </invoke>
   [... repeat for initiatives 2 through INIT_COUNT with incrementing initiative_number ...]
   </function_calls>
   ```

**Step 3.2: Poll for Phase 3 Completion**

Execute bash polling script (same pattern as Phase 2, change pattern to `phase3-step*.md`, EXPECTED_FILES = INIT_COUNT × 3)

**Step 3.3: Validate Phase 3 Output**

1. Count files: pattern `phase3-step*.md` → P3_COUNT
2. Expected: INIT_COUNT × 3
3. If mismatch, HALT
4. Log: `[timestamp] GSPM: Phase 3 validation passed (${P3_COUNT} files)`
5. Report to user: "✓ Phase 3: COMPLETE (${P3_COUNT} files)"

---

#### PHASE 4: Strategic Planning Launch

**Step 4.1: Launch Phase 4 Workers**

1. Capture timestamp → PHASE4_LAUNCH_TIME
2. EXPECTED_P4 = INIT_COUNT × 3
3. Log and report launch
4. Launch N Phase 4 workers in SINGLE message (same pattern as Phase 3)

**Step 4.2: Poll for Phase 4 Completion**

Execute bash polling script (pattern: `phase4-step*.md`, EXPECTED_FILES = INIT_COUNT × 3)

**Step 4.3: Validate Phase 4 Output**

1. Count files: pattern `phase4-step*.md` → P4_COUNT
2. Expected: INIT_COUNT × 3
3. If mismatch, HALT
4. Log validation passed
5. Report to user: "✓ Phase 4: COMPLETE (${P4_COUNT} files)"

---

#### PHASE 5: Program Planning Launch

**Step 5.1: Launch Phase 5 Workers**

1. Capture timestamp → PHASE5_LAUNCH_TIME
2. EXPECTED_P5 = INIT_COUNT × 4
3. Log and report launch
4. Launch N Phase 5 workers in SINGLE message (same pattern)

**Step 5.2: Poll for Phase 5 Completion**

Execute bash polling script (pattern: `phase5-step*.md`, EXPECTED_FILES = INIT_COUNT × 4)

**Step 5.3: Validate Phase 5 Output**

1. Count files: pattern `phase5-step*.md` → P5_COUNT
2. Expected: INIT_COUNT × 4
3. If mismatch, HALT
4. Log validation passed
5. Report to user: "✓ Phase 5: COMPLETE (${P5_COUNT} files)"

---

#### PHASE 6: Execution Planning Launch

**Step 6.1: Launch Phase 6 Workers**

1. Capture timestamp → PHASE6_LAUNCH_TIME
2. EXPECTED_P6 = INIT_COUNT × 3
3. Log and report launch
4. Launch N Phase 6 workers in SINGLE message (same pattern)

**Step 6.2: Poll for Phase 6 Completion**

Execute bash polling script (pattern: `phase6-step*.md`, EXPECTED_FILES = INIT_COUNT × 3)

**Step 6.3: Validate Phase 6 Output**

1. Count files: pattern `phase6-step*.md` → P6_COUNT
2. Expected: INIT_COUNT × 3
3. If mismatch, HALT
4. Log validation passed
5. Report to user: "✓ Phase 6: COMPLETE (${P6_COUNT} files)"

**Phases 2-6 Complete:**
- Total files: 4 + 17N (where N = INIT_COUNT)
- All phases validated with gate pattern
- Ready for final validation and reporting

---

### Step 4: Validation and Error Handling

#### 4.1 File Count Validation

For each completed phase, verify expected file counts:
- Phase 0: 3 files
- Phase 1: 1 + N files (where N = initiative count)
- Phase 2: N × 4 files
- Phase 3: N × 3 files
- Phase 4: N × 3 files
- Phase 5: N × 4 files
- Phase 6: N × 3 files

Use Glob tool to count files:
- P0_COUNT: Glob pattern `phase0-step*.md` in {output_folder} → count results
- P1_COUNT: Glob pattern `phase1-step*.md` in {output_folder} → count results
- P2_COUNT: Glob pattern `phase2-step*.md` in {output_folder} → count results
- P3_COUNT: Glob pattern `phase3-step*.md` in {output_folder} → count results
- P4_COUNT: Glob pattern `phase4-step*.md` in {output_folder} → count results
- P5_COUNT: Glob pattern `phase5-step*.md` in {output_folder} → count results
- P6_COUNT: Glob pattern `phase6-step*.md` in {output_folder} → count results

#### 4.2 Retry Logic (Max 3 Attempts Per Phase)

If validation fails:
1. Report specific failures to user
2. Ask: "Phase [N] validation failed. Retry? (yes/no)"
3. If yes and retry_count < 3:
   - Re-launch failed phase with failure feedback
   - Increment retry counter
   - Log retry attempt
4. If retry_count = 3:
   - Report: "Phase [N] failed after 3 attempts"
   - Ask user: "Continue to next phase or stop?"

#### 4.3 Graceful Failure Handling

If worker reports timeout waiting for dependencies:
- Log the failure with specific missing dependencies
- Report to user which initiative failed and why
- Provide troubleshooting guidance
- Ask whether to retry or continue with partial completion

---

### Step 5: Final Report and Execution Log

#### 5.1 Capture Final Timestamp
- Capture end time: `date +"%Y-%m-%d %H:%M:%S"` → END_TIME
- Calculate duration: END_TIME - START_TIME

#### 5.2 Update Execution Log

Append to log file:
```markdown
[${END_TIME}] GSPM: GenSI execution complete

---

## Summary

**Total Phases Executed:** 7 (0-6)
**Initiative Count:** ${INIT_COUNT}
**Total Files Created:** [Phase 0: 3, Phase 1: 1+${INIT_COUNT}, Phase 2: ${INIT_COUNT}×4, Phase 3: ${INIT_COUNT}×3, Phase 4: ${INIT_COUNT}×3, Phase 5: ${INIT_COUNT}×4, Phase 6: ${INIT_COUNT}×3]
**Total Execution Time:** [duration in minutes]
**Status:** Success

### File Manifest

**Phase 0 (Foundation):**
[list 3 files]

**Phase 1 (Initiatives):**
[list 1+N files]

**Phase 2 (Business Models):**
[list N×4 files]

**Phase 3 (Solution Design):**
[list N×3 files]

**Phase 4 (Strategic Planning):**
[list N×3 files]

**Phase 5 (Program Planning):**
[list N×4 files]

**Phase 6 (Execution Planning):**
[list N×3 files]

**Grand Total:** [4 + 17N files]
```

#### 5.3 Report to User

**Final report format:**

```
GenSI Execution Complete!

✓ Phase 0: Foundation research (3 files)
✓ Phase 1: Strategic initiatives (1+${INIT_COUNT} files)
✓ Phases 2-6: AAP execution (${INIT_COUNT}×17 = ${INIT_COUNT*17} files)

Total Files Created: ${4 + 17*INIT_COUNT}
Execution Time: [duration]
Architecture: Autonomous Agentic Pipeline (AAP) v2.0

Output Location: {output_folder}
Execution Log: {output_folder}/gensi-execution-log-${TIMESTAMP}.md

All artifacts created successfully and validated.
```

---

## Critical Implementation Rules

1. **NEVER execute phase work yourself** - You are the orchestrator only
2. **ALWAYS use Task tool with subagent_type: "worker"** - Enables parallelism
3. **Phase 1: Launch N templates in ONE message** - Parallel execution
4. **Phases 2-4: Launch N×3 templates in ONE message** - True AAP pattern
5. **ALWAYS use bash date for timestamps** - Claude fabricates timestamps in text
6. **ALWAYS use Write tool for logging** - NOT bash cat/echo redirects
7. **ALWAYS validate outputs before final report** - Quality gates required
8. **Template invocation is DYNAMIC** - Scales with actual initiative count N

---

## Error Handling

**If si-request file not found:**
- Report: "si-request file not found: {{arg1}}"
- Stop execution

**If invalid starting phase:**
- Report: "Invalid starting phase: {{arg2}}. Valid: 0, 1, 2, 3, 4"
- Stop execution

**If agent template files missing:**
- List missing templates
- Report: "Agent templates not found. Ensure FBLAI ADE extension synced."
- Stop execution

**If prior phase outputs missing:**
- List missing files with expected counts
- Report: "Required prior phase outputs not found."
- Stop execution

**If worker timeout (AAP dependency wait exceeded 10 minutes):**
- Log which worker(s) timed out
- Report specific missing dependencies
- Provide troubleshooting: "Check if Phase [N-1] worker for this initiative completed"
- Ask user whether to retry or continue with partial completion

**If validation fails after 3 retries:**
- Report detailed failure information
- Ask: "Continue to next phase with failed artifacts or stop?"
- Log user decision

---

## Performance Monitoring

Log these metrics for analysis:
- Phase 0 duration
- Phase 1 duration (should show ~75% improvement vs sequential)
- AAP launch time
- AAP completion time (should show pipeline overlapping)
- Total execution time
- Per-initiative completion times (if reported by workers)

Expected improvements (vs sequential execution):
- Phase 1: 75-80% faster (59 min → 12-15 min for N=5)
- Phases 2-4: 40-60% faster through pipeline overlapping
- Total: 40-60% faster end-to-end

---

**Now execute the GenSI AAP orchestration process according to these instructions.**
