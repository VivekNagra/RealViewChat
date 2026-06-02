# RealView — system diagrams

These are the UML and architecture diagrams for RealView, written in [Mermaid](https://mermaid.js.org/). **GitHub renders them directly on this page** — no tools to install, and you can click any diagram to zoom in far beyond what the PDF allows. They are the same diagrams as Figures 7–10 in the bachelor report, and they are derived from the code in this repository.

## Domain model (report Figure 7)

The relational data model expressed as a domain-driven-design aggregate. **Property** is the aggregate root; its **PipelineRun**, **Image**, **ImageFeature**, **Room** and **RoomFeature** form a single consistency boundary that is written atomically by `persist_property_aggregate`. **Feedback** references a property but is persisted separately, after human review.

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#ffffff","primaryTextColor":"#000000","primaryBorderColor":"#000000","lineColor":"#000000","textColor":"#000000","mainBkg":"#ffffff","nodeBorder":"#000000","clusterBkg":"#ffffff","clusterBorder":"#000000","edgeLabelBackground":"#ffffff","noteBkgColor":"#ffffff","noteTextColor":"#000000","noteBorderColor":"#000000","actorBkg":"#ffffff","actorBorder":"#000000","actorTextColor":"#000000","actorLineColor":"#000000","signalColor":"#000000","signalTextColor":"#000000","labelBoxBkgColor":"#ffffff","labelBoxBorderColor":"#000000","labelTextColor":"#000000","loopTextColor":"#000000","activationBkgColor":"#ffffff","activationBorderColor":"#000000","classText":"#000000"}} }%%
classDiagram
    direction TB

    namespace PropertyAggregate {
        class Property {
            <<aggregate root>>
            +property_id : string
            +created_at
            +updated_at
        }
        class PipelineRun {
            +status : running/completed/failed
            +model_name
            +duration_seconds
            +raw_output : JSONB
        }
        class Image {
            +filename
            +room_type
            +actionable
            +condition_score : 1-5
            +modernity_score : 1-5
            +material_score : 1-5
            +functionality_score : 1-5
        }
        class ImageFeature {
            +feature_id
            +severity : low/medium/high
            +confidence : 0-1
            +explanation
        }
        class Room {
            +room_type
            +condition_score : 1-5
            +modernity_score : 1-5
            +material_score : 1-5
            +functionality_score : 1-5
        }
        class RoomFeature {
            +feature_id
            +severity
            +confidence
            +evidence
        }
    }

    class Feedback {
        <<separate write>>
        +feedback_type : classification/verdict/score
        +verdict
        +classification
        +score_type
        +score_value
        +created_at
    }

    Property "1" *-- "0..*" PipelineRun : runs
    Property "1" *-- "0..*" Image : images
    Property "1" *-- "0..*" Room : rooms
    Image "1" *-- "0..*" ImageFeature : features
    Room "1" *-- "0..*" RoomFeature : features
    PipelineRun "1" ..> "0..*" Image : produced in
    PipelineRun "1" ..> "0..*" Room : produced in
    Feedback "0..*" ..> "1" Property : references
    Feedback "0..*" ..> "0..1" Image : may reference

    note "PropertyAggregate is one consistency boundary, persisted atomically by persist_property_aggregate (property to pipeline_run to images to image_features to rooms to room_features). property_id is the unique external key. Reviewer Feedback references a Property but is written separately, after human review."
```

## Asynchronous inspection sequence (report Figure 8)

The end-to-end flow of one inspection. The API returns `202 Accepted` and publishes a job to RabbitMQ; the worker consumes it, runs the Vision passes (`pass1`/`pass2`/`pass2.5`), persists the whole aggregate in a single transaction, and acknowledges the message **only after** the commit. The retry → dead-letter branch and the idempotent handling of duplicate delivery are shown as well.

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#ffffff","primaryTextColor":"#000000","primaryBorderColor":"#000000","lineColor":"#000000","textColor":"#000000","mainBkg":"#ffffff","nodeBorder":"#000000","clusterBkg":"#ffffff","clusterBorder":"#000000","edgeLabelBackground":"#ffffff","noteBkgColor":"#ffffff","noteTextColor":"#000000","noteBorderColor":"#000000","actorBkg":"#ffffff","actorBorder":"#000000","actorTextColor":"#000000","actorLineColor":"#000000","signalColor":"#000000","signalTextColor":"#000000","labelBoxBkgColor":"#ffffff","labelBoxBorderColor":"#000000","labelTextColor":"#000000","loopTextColor":"#000000","activationBkgColor":"#ffffff","activationBorderColor":"#000000","classText":"#000000"}} }%%
sequenceDiagram
    actor R as Reviewer (frontend)
    participant API as Flask API
    participant MQ as RabbitMQ broker
    participant W as Worker
    participant V as LLMClient / OpenAI Vision
    participant DB as PostgreSQL

    R->>API: POST /api/inspections {property_id}
    API->>MQ: publish {property_id}
    alt broker reachable
        API-->>R: 202 Accepted
    else broker unavailable
        API-->>R: error (no false 202)
    end

    MQ->>W: deliver {property_id}
    W->>V: pass1, pass2, pass2.5 (classify, detect + score, consolidate)
    V-->>W: per-image and room results
    W->>DB: persist_property_aggregate (single transaction)
    Note over W,DB: property to pipeline_run to images to image_features to rooms to room_features
    DB-->>W: commit OK
    W->>MQ: ack (only after commit)

    alt transient failure (Vision or DB)
        W->>MQ: republish with x-retry-count + 1, ack original
        Note over W,MQ: at MAX_RETRIES, nack(requeue=false) to dead-letter queue
    end
    Note over MQ,DB: duplicate delivery is safe: UNIQUE property_id + pre-insert check (idempotent)
```

## LLMClient seam (report Figure 9)

The dependency-injection seam around the OpenAI Vision calls. `LLMClient` is a `Protocol` (`pass1`/`pass2`/`pass25`) realised by the real `OpenAIBackend` and the deterministic `FakeVisionClient`. The pipeline depends only on the Protocol, so the test suite runs with no network and no API cost.

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#ffffff","primaryTextColor":"#000000","primaryBorderColor":"#000000","lineColor":"#000000","textColor":"#000000","mainBkg":"#ffffff","nodeBorder":"#000000","clusterBkg":"#ffffff","clusterBorder":"#000000","edgeLabelBackground":"#ffffff","noteBkgColor":"#ffffff","noteTextColor":"#000000","noteBorderColor":"#000000","actorBkg":"#ffffff","actorBorder":"#000000","actorTextColor":"#000000","actorLineColor":"#000000","signalColor":"#000000","signalTextColor":"#000000","labelBoxBkgColor":"#ffffff","labelBoxBorderColor":"#000000","labelTextColor":"#000000","loopTextColor":"#000000","activationBkgColor":"#ffffff","activationBorderColor":"#000000","classText":"#000000"}} }%%
classDiagram
    direction TB

    class LLMClient {
        <<Protocol>>
        +pass1(image_data_url) dict
        +pass2(image_data_url) dict
        +pass25(room_type, image_data_urls) dict
    }
    class OpenAIBackend {
        -client : OpenAI
        -model : string
        -rate_limiter : RateLimiter
        -max_retries : int
        +pass1(image_data_url) dict
        +pass2(image_data_url) dict
        +pass25(room_type, image_data_urls) dict
    }
    class FakeVisionClient {
        -_pass1 : dict
        -_pass2 : dict
        -_pass25 : dict
        +calls : dict
        +pass1(image_data_url) dict
        +pass2(image_data_url) dict
        +pass25(room_type, image_data_urls) dict
    }
    class PipelinePasses {
        <<module>>
        +run_pass1(client, image_data_url) Pass1Result
        +run_pass2(client, image_data_url) Pass2Result
        +run_pass25(client, room_type, urls) Pass25Result
    }
    class create_client {
        <<factory>>
        +create_client(config) LLMClient
    }

    LLMClient <|.. OpenAIBackend : realizes
    LLMClient <|.. FakeVisionClient : realizes (test double)
    PipelinePasses ..> LLMClient : depends on
    create_client ..> OpenAIBackend : builds
    create_client ..> LLMClient : returns

    note for LLMClient "Dependency-injection seam: production code receives the real OpenAIBackend;<br/>tests inject the deterministic FakeVisionClient.<br/>The pipeline depends only on this Protocol,<br/>so tests run with no network and no API cost."
```

## Use case overview (report Figure 10)

The reviewer's interactions with the system — submitting inspections, viewing flagged properties, and reviewing or correcting the AI output — with the OpenAI Vision API as a secondary actor. (Mermaid has no native UML use-case shape, so actors are drawn as boxes and use cases as rounded nodes.)

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#ffffff","primaryTextColor":"#000000","primaryBorderColor":"#000000","lineColor":"#000000","textColor":"#000000","mainBkg":"#ffffff","nodeBorder":"#000000","clusterBkg":"#ffffff","clusterBorder":"#000000","edgeLabelBackground":"#ffffff","noteBkgColor":"#ffffff","noteTextColor":"#000000","noteBorderColor":"#000000","actorBkg":"#ffffff","actorBorder":"#000000","actorTextColor":"#000000","actorLineColor":"#000000","signalColor":"#000000","signalTextColor":"#000000","labelBoxBkgColor":"#ffffff","labelBoxBorderColor":"#000000","labelTextColor":"#000000","loopTextColor":"#000000","activationBkgColor":"#ffffff","activationBorderColor":"#000000","classText":"#000000"}} }%%
flowchart LR
    reviewer["Reviewer"]:::actor
    vision["OpenAI Vision API"]:::ext

    subgraph sys["RealView system"]
        direction TB
        uc1(["Submit inspection"])
        uc2(["View flagged properties"])
        uc3(["Review room classification"])
        uc4(["Confirm / correct detected damage"])
        uc5(["Adjust quality scores"])
        uc6(["Analyse images: pass1 / pass2 / pass2.5"])
    end

    reviewer --- uc1
    reviewer --- uc2
    reviewer --- uc3
    reviewer --- uc4
    reviewer --- uc5
    uc1 -. triggers .-> uc6
    uc6 --- vision

    classDef actor fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000,font-weight:bold
    classDef ext fill:#ffffff,stroke:#000000,stroke-width:1.5px,color:#000000,stroke-dasharray:5 3
```
