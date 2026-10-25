# AWS step-by-step: from a new account to a running, viewable project

Everything happens in **Asia Pacific (Mumbai) `ap-south-1`**. If the console ever looks empty,
check the region selector first (top right of every console page).

Commands are for **PowerShell**, run from the project's **backend** folder unless a step says
otherwise:

```
cd "C:\Sandhya Venkatesh\OneDrive\Desktop\Documents\GitHub\aws-project\backend"
```

---

## Part 1: Create and secure the AWS account (about 20 min)

1. Go to https://aws.amazon.com and click **Create an AWS Account** (orange button, top right).
2. Enter your **root email** and an **AWS account name** (e.g. `sandhya-etl`), then click
   **Verify email address**. Type the code from the email and click **Verify**.
3. Create the **root password** and click **Continue**.
4. When asked for a plan, choose the **Free plan**.
5. Contact information: choose **Personal**, fill it in, tick the agreement, then click **Continue**.
6. Billing information: enter a card. AWS places a small verification hold. Click
   **Verify and continue** and complete the OTP from your bank.
7. Identity verification: enter your phone number and the SMS code.
8. Support plan: choose **Basic support – Free**, then click **Complete sign up**.
9. Click **Go to the AWS Management Console**, choose **Root user**, and sign in with your email and
   password.
10. **Set the region.** At the top right, between the **?** icon and your account name, there is a
    region name (for example "N. Virginia"). Click it and choose **Asia Pacific → Mumbai
    (ap-south-1)**.
11. **Turn on MFA for root.** Click your **account name** (top right), then **Security credentials**.
    Under "Multi-factor authentication (MFA)", click **Assign MFA device**. Name it `root-phone`,
    choose **Authenticator app**, then click **Next** and **Show QR code**. Scan it with Google
    Authenticator or Microsoft Authenticator. Type the current code in **MFA code 1**, wait for
    the next code and type it in **MFA code 2**, then click **Add MFA**.
12. **Create the $5 budget.** In the search bar at the top, type `Budgets` and open
    **Budgets**. Click **Create budget** (orange, right side) and work through these screens:
    1. Choose **Customize (advanced)** and **Cost budget**, then click **Next**.
    2. Budget name `monthly-5-usd`. Period **Monthly**, **Recurring budget**, **Fixed**. Amount **5**.
       Click **Next**.
    3. Click **Add an alert threshold**: threshold `50`, **% of budgeted amount**, trigger
       **Actual**, email recipients = your email.
    4. Click **Add an alert threshold** again for `80`, and once more for `100`.
    5. Click **Next**, then **Next** again (skip actions), then **Create budget**.
13. **Free Tier alerts.** Search `Billing` and open **Billing and Cost Management**. In the left menu,
    click **Billing preferences**. On the **Alert preferences** panel, click **Edit**, tick
    **Receive AWS Free Tier alerts**, enter your email, then click **Update**.

## Part 2: Create the deploy identity `etl-dev` (about 5 min)

SAM creates IAM roles, so this user needs administrator rights. The public website needs no
IAM user at all: it reads through a read-only API.

1. Search `IAM` and open **IAM**. In the left menu, click **Users**, then **Create user** (orange,
   top right).
2. User name `etl-dev`. Leave **Provide user access to the AWS Management Console** unticked.
   Click **Next**.
3. Choose **Attach policies directly**. Type `AdministratorAccess` in the search box and tick the
   checkbox next to **AdministratorAccess**. Click **Next**, then **Create user**.
4. Open the access key page: click **etl-dev** in the user list, open the **Security credentials**
   tab, scroll to **Access keys**, and click **Create access key**.
5. Use case **Command Line Interface (CLI)**. Tick the confirmation box and click **Next**.
6. Description `laptop`. Click **Create access key**, then **Download .csv file**. Keep this file
   **outside** the project folder and never commit it. Click **Done**.

## Part 3: Install the tools on your laptop (about 15 min)

1. **AWS CLI v2:** download https://awscli.amazonaws.com/AWSCLIV2.msi, run it and click through
   **Next → Install → Finish**.
2. **AWS SAM CLI:** download
   https://github.com/aws/aws-sam-cli/releases/latest/download/AWS_SAM_CLI_64_PY3.msi and install
   it the same way.
3. **Python 3.12**, which `sam build` needs. In PowerShell:

   ```
   py install 3.12
   ```

   If that command is not recognised, install Python 3.12 from https://www.python.org/downloads/
   and tick **Add python.exe to PATH**.
4. **Close and reopen PowerShell**, then check all three:

   ```
   aws --version
   ```
   ```
   sam --version
   ```
   ```
   py -3.12 --version
   ```
5. **Connect the CLI to your account.** Run the command below and paste the values from the
   `.csv` file. Region `ap-south-1`, output `json`.

   ```
   aws configure --profile etl-dev
   ```
6. **Check the connection.** You should see your 12-digit account number and `user/etl-dev`:

   ```
   aws sts get-caller-identity --profile etl-dev
   ```

## Part 4: Check the project locally (about 5 min)

The `.venv` already exists on this laptop. On a new machine, run these from the project root
first: `python -m venv .venv`, then `.venv\Scripts\pip install -r backend\requirements-dev.txt`,
then `cd frontend` and `npm ci`. You also need **Node.js 20+** (`node --version`).

Backend tests (from `backend`):

```
..\.venv\Scripts\python -m pytest -q
```

Frontend tests (from `frontend`):

```
npm test
```

Every test should pass.

## Part 5: Deploy the project to AWS (about 10 min)

1. Validate the template:

   ```
   sam validate --lint
   ```
2. Build it:

   ```
   sam build
   ```

   If this fails with *"Binary validation failed for python"*, delete the `.aws-sam` folder and
   skip to step 3. The code has no third-party packages, so deploying without a build works.
3. Deploy, replacing `sandhya.avm@gmail.com` with your address:

   ```
   sam deploy --parameter-overrides Stage=dev EnableAnalytics=false AlertEmail=sandhya.avm@gmail.com
   ```
4. SAM prints a table of everything it will create and asks
   `Deploy this changeset? [y/N]:`. Type **y** and press Enter.
5. Wait 3–5 minutes for `Successfully created/updated stack - serverless-etl-dev in ap-south-1`.
   The **Outputs** table at the end shows your bucket, queue, table and function names.
6. Check your email for **"AWS Notification - Subscription Confirmation"** and click
   **Confirm subscription**. Alarm emails will not arrive without this.
7. **See it in the console:** search `CloudFormation` and open **Stacks**, then
   **serverless-etl-dev**. Status should be **CREATE_COMPLETE**. The **Resources** tab lists
   everything that was created; the **Outputs** tab shows the names.

## Part 6: Run the automatic ingestion and watch the data flow (about 10 min)

The schedule runs every day at 06:30 IST. To run it now:

```
..\.venv\Scripts\python scripts\ingest_now.py
```

You should see `global_temperature uploaded` and `world_population uploaded`. Wait about 30
seconds, then look at the results in the console.

- **S3:** search `S3` and open **General purpose buckets**, then
  **etl-pipeline-<account>-ap-south-1**. You'll see the folders `raw/`, `processed/`, `rejected/`
  and `reports/`.
  - Open `reports/` → `dataset=world_population/` → the `.json` file, then click **Open** (top
    right) to read the report.
  - Open `raw/` → `world_population/` → the `.csv` file → **Properties** tab → **Metadata**. You
    will see the `sha256` and `source-url` the ingester recorded.
- **DynamoDB:** search `DynamoDB` and click **Tables** (left), then **etl-jobs-dev**, then
  **Explore table items** (top right). Each job shows status **SUCCEEDED**, row counts and timings.
- **Lambda logs:** search `Lambda`, open **Functions**, then **etl-processor-dev**. Open the
  **Monitor** tab, click **View CloudWatch logs**, and open the newest log stream. You'll see one
  JSON line per stage (`job_claimed`, `job_finished`, …).
- **Schedule:** search `EventBridge`. In the left menu, under **Scheduler**, click **Schedules**,
  then **dataset-ingest-dev**. It shows `cron(30 6 * * ? *)` in Asia/Kolkata.

Now run `ingest_now.py` a second time. Both datasets say **unchanged** and no new jobs appear,
because the change detection is working.

## Part 7: CloudWatch dashboard, alarms and logs (about 5 min)

1. Search `CloudWatch`. In the left menu, click **Dashboards**, then **etl-pipeline-dev**. The
   custom metrics appear 1–3 minutes after the first jobs.
2. In the left menu, open **Alarms → All alarms**. You'll see 4 alarms: `lambda-errors`,
   `dlq-not-empty`, `queue-stuck` and `ingest-failed`. They show **OK**, or **Insufficient data**
   at first.
3. In the left menu, open **Logs → Logs Insights**. In the right panel, open **Queries** (saved
   queries), choose **etl-pipeline-dev/recent-jobs**, then click **Run query**.

## Part 8: Publish and open the React website (about 5 min)

The stack from Part 5 already created the website's AWS pieces: an **Amplify** app, an **API
Gateway** API and the read-only **API Lambda**. Now publish the React app into it:

1. From the `backend` folder, run:

   ```
   ..\.venv\Scripts\python scripts\deploy_frontend.py
   ```
   It builds the React app, uploads it to Amplify, waits about 30 seconds, and prints
   `Deployed. Live at https://main.<id>.amplifyapp.com`.
2. Open that URL in any browser (phone too) and look around:
   - **Overview:** files processed, failed jobs, acceptance rate, charts, latest jobs.
   - **Analytics:** pick *World population* (world, India and China lines, largest countries)
     or *Global temperature* (yearly anomaly per record).
   - **Sources:** the two public datasets, their licenses and their latest ingestion.
   - **Jobs:** click any job to see its report and its rejected rows.
   - **Data quality, Operations, Athena SQL, How it works:** rejection reasons, queue health,
     SQL results, and the architecture explained.
3. **See it in the console:** search `Amplify` and open **etl-dashboard-dev** → **main** for
   the deployment history and domain. Search `API Gateway` and open the HTTP API to see the
   `GET /api/{proxy+}` route and its throttling.

The website is **read-only**: visitors can look, but nothing on the site can upload, delete or
change data.

## Part 9: Demo scenarios, your evidence (about 40 min, mostly waiting)

Take a screenshot of each result and save it in `docs\evidence\`.

| # | Do this | What you should see |
|---|---|---|
| 1 | `..\.venv\Scripts\python scripts\upload.py sample_data\clean_small.csv --wait` | `SUCCEEDED`, 100 of 100 rows accepted |
| 2 | `..\.venv\Scripts\python scripts\upload.py sample_data\dirty_small.csv --wait` | 950 accepted; 10 rows each for 5 reason codes (Data quality page) |
| 3 | `..\.venv\Scripts\python scripts\replay_event.py raw/sales/dirty_small.csv --times 3` | the script prints `IDEMPOTENT: nothing changed` |
| 4 | `..\.venv\Scripts\python scripts\upload.py sample_data\poison.csv` | job `FAILED` right away; after **12–18 min** one message in the DLQ, alarm **In alarm**, and an email |
| 5 | Recovery, see below | the failed `returns` job becomes `SUCCEEDED` with attempts = 4 |
| 6 | `..\.venv\Scripts\python scripts\run_benchmark.py` (about 10 min) | writes `docs\evidence\benchmark.md` with real timings |

**Uploading by clicking instead (optional):** open the bucket, then `raw/` →
**Create folder** `sales` → open it → **Upload** → **Add files** → choose a CSV → **Upload**.

**Seeing the DLQ (scenario 4):** search `SQS` and open **Queues**, then **etl-jobs-dlq-dev**.
**Messages available** shows 1.

**Scenario 5, recovery.** Start step 1 together with scenario 4 so the waits overlap.

1. Upload a dataset that has no schema yet:

   ```
   ..\.venv\Scripts\python scripts\upload.py sample_data\returns_small.csv --dataset returns
   ```
2. After 12–18 minutes it is in the DLQ. Ship the fix: copy `demo\returns.json` into
   `src\schemas\`, then run `sam build` and the same `sam deploy` command as in Part 5.
   (This has been done once already, so `src\schemas\returns.json` exists. To repeat the
   demo, delete it and deploy first.)
3. Redrive by clicking: open **SQS → etl-jobs-dlq-dev** and click **Start DLQ redrive** (top
   right). Choose **Redrive to source queue(s)** and click **DLQ redrive**.
4. The `returns` job ends **SUCCEEDED** with attempts **4**. The poison file fails again and goes
   back to the DLQ, which is expected because the file itself is broken.

## Part 10 (optional): V2 – Parquet and Athena

1. Search `Athena` and open it once, then do the same for `Glue`. If either says it is not
   available on your plan, skip Part 10.
2. Redeploy with analytics on:

   ```
   sam deploy --parameter-overrides Stage=dev EnableAnalytics=true AlertEmail=sandhya.avm@gmail.com
   ```
3. Process a file under a **new name**, so it gets Parquet output:

   ```
   ..\.venv\Scripts\python scripts\upload.py sample_data\dirty_small.csv --name dirty_v2.csv --wait
   ```
4. **Athena console → Query editor.** Set the **Workgroup** dropdown (top right) to
   **etl-pipeline-dev** and acknowledge the settings if asked. On the left, set Database to
   **etl_pipeline_dev**. Open the **Saved queries** tab, click **monthly-revenue**, then **Run**.
5. Measure CSV against Parquet. This writes `docs\evidence\athena-comparison.md`:

   ```
   ..\.venv\Scripts\python scripts\compare_formats.py --job-id <job_id printed in step 3>
   ```
6. Open the website's **Athena SQL** page. The three saved queries now return results.

## Part 11: See CI run on GitHub

After you commit and push (GitHub Desktop → **Push origin**), open
https://github.com/sandhyav6/aws-project and click the **Actions** tab. The **CI** run should
turn green, and the README badge updates.

## Part 12: Clean up when the course is over

1. Empty the bucket. It asks you to type `DELETE` to confirm:

   ```
   ..\.venv\Scripts\python scripts\teardown.py
   ```
2. Delete the stack. This also removes the website, the API and the Amplify app:

   ```
   sam delete
   ```
3. In **CloudFormation → Stacks**, if **aws-sam-cli-managed-default** remains, empty its S3
   bucket first (**S3 → bucket → Empty**), then delete that stack.
4. Sign in as root and delete the `etl-dev` user's access key (or the whole user).
5. Check **Billing and Cost Management → Bills** over the next days for anything left.

## If something goes wrong

| Problem | Fix |
|---|---|
| Console shows no bucket, table or functions | The region selector (top right) is not **Mumbai** |
| `Unable to locate credentials` / `ProfileNotFound` | Run `aws configure --profile etl-dev` again |
| Stack is in `ROLLBACK_COMPLETE` | Run `sam delete`, fix the cause, and deploy again. Copy the first red `CREATE_FAILED` reason from **CloudFormation → stack → Events** and send it to Claude |
| An error says a service is not available on the Free plan | Leave that feature off (for V2, keep `EnableAnalytics=false`) |
| Website shows "Could not load this data" | Run `scripts\deploy_frontend.py` again after any `sam deploy`; check the API in **API Gateway → Stages** |
| `Your account must be verified before you can add new CloudFront resources` | New accounts can't create CloudFront yet; this project uses Amplify Hosting instead (ADR 008) |
| No alarm email | You haven't clicked **Confirm subscription** in the AWS email |
