import os

os.environ["GROQ_API_KEY"] = ""

import app  # noqa: E402


def main():
    client = app.app.test_client()
    samples = [
        (
            "ai-demo",
            "Artificial intelligence represents a transformative paradigm shift in modern society. "
            "It is important to note that while the benefits are numerous, stakeholders across "
            "various sectors must collaborate to ensure responsible deployment.",
        ),
        (
            "human-demo",
            "ok so i finally tried that new ramen place downtown and honestly it was underwhelming. "
            "the broth was fine but they put way too much sodium in it and i was thirsty for hours after.",
        ),
        (
            "borderline-demo",
            "The relationship between monetary policy and asset price inflation has been extensively "
            "studied in the literature. Central banks face a fundamental tension between price stability "
            "and prolonged low interest rates.",
        ),
    ]

    content_ids = []
    for creator_id, text in samples:
        response = client.post("/submit", json={"creator_id": creator_id, "text": text})
        body = response.get_json()
        print(
            f"submit {response.status_code}: {body['attribution']} "
            f"confidence={body['confidence']} content_id={body['content_id']}"
        )
        content_ids.append(body["content_id"])

    appeal = client.post(
        "/appeal",
        json={
            "content_id": content_ids[0],
            "creator_reasoning": "I wrote this myself and can provide revision drafts.",
        },
    )
    print(f"appeal {appeal.status_code}: status={appeal.get_json()['status']}")

    log_entries = client.get("/log").get_json()["entries"]
    print(f"log entries visible: {len(log_entries)}")
    print(f"last event: {log_entries[-1]['event']} status={log_entries[-1]['status']}")


if __name__ == "__main__":
    main()

