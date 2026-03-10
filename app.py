from flask import Flask, render_template, request, jsonify
from scheduler import generate_schedule, summarize_schedule
from datetime import datetime, date, timedelta

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json

    men_enabled = data.get("menEnabled", False)
    women_enabled = data.get("womenEnabled", False)

    men_count = int(data.get("menCount") or 0)
    women_count = int(data.get("womenCount") or 0)

    start_time = data.get("startTime")
    end_time = data.get("endTime")
    placement = data.get("placement", "mixed_alternating")

    if not start_time or not end_time:
        return jsonify({"error": "Please choose both a start time and an end time."}), 400

    if not men_enabled and not women_enabled:
        return jsonify({"error": "Please select at least one league."}), 400

    if men_enabled and men_count != 16:
        return jsonify({"error": "This version currently requires exactly 16 men."}), 400

    if women_enabled and women_count != 8:
        return jsonify({"error": "This version currently requires exactly 8 women."}), 400

    men = [str(i + 1) for i in range(men_count)] if men_enabled else []
    women = [chr(65 + i) for i in range(women_count)] if women_enabled else []

    tee_times = []
    start = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")

    while start <= end:
        tee_times.append(start.strftime("%-I:%M"))
        start += timedelta(minutes=8)

    if len(tee_times) != 6:
        return jsonify({
            "error": f"This version currently requires exactly 6 tee times. You created {len(tee_times)}."
        }), 400

    try:
        season = generate_schedule(
            men_players=men,
            women_players=women,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 9, 16),
            tee_times=tee_times,
            placement_mode=placement,
        )

        summary = summarize_schedule(season, men, women)

        return jsonify({
            "season": [week.__dict__ for week in season],
            "summary": summary
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True)