# Building Sentinel: Securing the Future of Automated Credit Decisions

Everything these days is being automated, and the tendency to solve everything with AI has been increasing every day. One question remains: How do we protect privacy? 

The project we have been working on Sentinel and it is focused on how banks automate the loan and credit approval procedure. Normally, when you apply for something like an American Express card, you fill out an online form with details like your SSN and income information, and sometimes upload paystubs or bank statements. You instantly get a decision with an offer, and then you decide whether to accept it or not.

In the fintech world, this entire process happens through automation. The current trend is to use Large Language Models (LLMs), but the problem is that you are providing sensitive information to the LLM when you call the API. The LLM sees your private information to make the approval decision. 

We often hear about sensitive info leaking, but how does it actually leak? Personal data is at risk whenever you use standard API calls. Big tech companies are constant targets for malicious attacks, and that's when your data can get stolen. Furthermore, LLMs often store data to train and improve, so when you send sensitive information, it gets stored, at least within the context.

This is where Sentinel comes in. It acts as a shield, a layer that filters out personal information and acts as an orchestrator. It ensures the AI only receives exactly what it needs to know to evaluate an applicant. I will walk you through exactly how this works.

## The Three Pillars: Scalability, Resilience, and Security

To build a system like this, we kept three core pillars in mind:

### 1. Scalability
Can we scale this idea to a large user base? Do we have the necessary resources? Most importantly, can we guarantee that all personal data is handled securely before it ever reaches the LLM? For us, scalability isn't just about handling more users; it's about scaling our privacy promise alongside our processing power.

### 2. Resilience
Will the system recover if there is a potential leak or an operational error? We focused on setting up guardrails to ensure the pipeline is healthy at every step of the way. We also integrated deep observability monitoring PII detection rates, files processed, and the time taken for each stage of the pipeline. This level of monitoring ensures the system is resilient and reliable.

### 3. Security
How do we ensure the entire process is truly secure? We practiced strict data minimization and backed our code with rigorous testing, from unit tests to smoke tests. We looked closely at the raw input files entering the system and strictly controlled exactly what the LLM is allowed to see. This is the heart of our security architecture.

## The Engine Room: How It All Runs So Fast

Before we look at the document journey, you might wonder how does this system handle hundreds of people at once without crashing? 

Think about Instagram. When you double-tap a photo to "like" it, it feels instant, right? But in the background, a lot is happening. Instagram uses things like **Redis** and **Celery** to handle all those likes. 

In our project, Redis is like the "waiting room" or the queue. When you upload your files, you don't want to sit there staring at a loading screen for minutes while the AI thinks. So, we drop your job into the Redis queue. Then, Celery (our "workers") picks up the jobs one by one and processes them in the background. This happens so fast that the app stays responsive, just like the apps you use every day.

## The Architecture: The Big Picture

Here’s how all those pieces the frontend, the queue, and the workers actually talk to each other. It looks like a lot, but it’s really just a relay race where each part does its job and passes the baton to the next.

```mermaid
graph TD
    %% Frontend
    UI["💻 Streamlit Frontend<br/>Batch Upload · Track · Review Queue<br/>Score breakdown · Redaction preview"]

    %% API
    API["⚙️ FastAPI<br/>Batch Upload · Status · Results<br/>Redacted preview · Review queue"]

    %% Queue
    Q["📥 Redis<br/>Job queue / broker"]

    %% Pipeline steps
    subgraph PIPELINE [Celery Worker — Pipeline]
        P1["📄 Parse<br/>pdfplumber → text"]
        P2["🔍 Authenticate<br/>Balance math · PDF metadata<br/>Fraud detection"]
        P3["🛡️ Redact<br/>Presidio + spaCy<br/>PII → typed placeholders"]
        P4["🤖 Gemini 2.5 Flash<br/>Structured extraction<br/>Risk flags · Findings"]
        P5["📊 Score<br/>Deterministic 100-pt scorecard<br/>Reason codes · Hard stops"]
        P6["🗑️ Cleanup<br/>Delete raw PDF + parsed text<br/>Data minimization"]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6
    end

    %% Data layer
    subgraph DATA [Data Layer]
        S3["🗄️ MinIO / GCS<br/>Redacted text · Reports<br/>Extraction · Score breakdown"]
        PG["🐘 Postgres / Cloud SQL<br/>Job status · Confidence score<br/>Auth result · Entity counts"]
    end

    %% Observability
    subgraph OBS [Observability]
        PROM["🔥 Prometheus"]
        GRAF["📊 Grafana<br/>15-panel dashboard"]
    end

    %% Flow
    UI -->|"Upload Batch"| API
    API -->|"Store raw PDF"| S3
    API -->|"Create job records"| PG
    API -->|"Enqueue jobs"| Q
    Q --> P1
    P5 -->|"Store artifacts"| S3
    P5 -->|"Update score + status"| PG
    API -->|"GET /batches/{id}"| PG
    API -->|"GET /jobs/{id}/results"| S3
    API -->|"Results + preview"| UI

    API -->|metrics| PROM
    PIPELINE -->|metrics| PROM
    PROM --> GRAF
```

## The Anatomy of the Pipeline: How it Works

Let me walk you through the actual journey of a document. It’s a 4-phase process that keeps everything clean and secure.

### Phase 1: Ingestion & Guardrails
First, we ingest the raw data. But before we do anything, we have "guardrails" to check: Is this even a financial document? Is it legit? If it’s not, we just reject it right there. This saves us from wasting resources on parsing, redacting, or making expensive LLM API calls. We authenticate first so we only process what matters.

### Phase 2: The Privacy Guard
This is where we use spaCy and Presidio (Microsoft’s open source SDK). These are NLP engines that find PII through text vectorization. Now, you might think wait, if we’re using Microsoft’s stuff, isn't the data at risk? Actually, no. We aren't making API calls to them. We install the SDK and libraries locally. It's our own instance, no internet involved. 

We set up spaCy to do the heavy lifting, and just in case it misses something, Presidio acts like a second scan or an inspection. It’s fast, efficient, and proves that we can solve the privacy problem with the right setup.

### Phase 3: Controlled Extraction
Now that the file is redacted, Gemini (Vertex AI) takes a look. It sees something like: "Okay, the user’s income is [REDACTED], and their transactions look clean." It extracts the structured info we need and passes it to the next stage to see how they do on the scoreboard.

### Phase 4: Deterministic Scoring
This is where we follow the standard rules fintechs use. We check for overdrafts, shady transactions, and income proof. Everything turns into a 100-point scoring system. 

Right now, the threshold is 80. If you score 80+, you’re automatically approved. If files are missing or the score is lower, it goes to a human reviewer to check the files. And if things look really shady, it’s a total rejection. This way, we aren't just letting the AI decide everything—we’re using math and giving people a real explanation if they don't get the offer.

## Moving to the Cloud: The Real Challenge

Moving this whole thing from my laptop to Google Cloud was a big step. You know how people always say "It works on my machine"? Well, the cloud is a totally different world. We had to "dumb it down" and make sure all our different parts could talk to each other in the cloud just like they did at home. 

One of the most important things was handling our API keys. These keys are like the password to our Gemini AI, so they’re super sensitive. We couldn't just leave them sitting in the code where anyone could see them. 

Instead, we used **GitHub Actions secrets** to handle the deployment and **Google Secret Manager** to store the keys in a high-tech digital vault. This means the actual keys are never in our source code. When the system starts up in the cloud, it "requests" the keys it needs on the fly. We also set it up so that each part of our system only has permission to see the specific keys it needs to do its job nothing more. This way, the information is only accessed when it's absolutely necessary, and it stays protected in its vault otherwise.

## The Finish Line

Building this was a huge learning curve. The Minimum Viable Product (MVP) is on par and is performing as expected. It could be more refined by having rulesets and criteria like how many files do we accept for proof of income? Is it just paystubs or bank statements too? Do we need W2 forms? Having clear criteria helps us build a policy first, which we can then use in our software to evaluate our customers and build that mutual trust and grow in business.

Feel free to run the demo, test it and play around: https://sentinel-frontend-1041799394320.us-central1.run.app/
