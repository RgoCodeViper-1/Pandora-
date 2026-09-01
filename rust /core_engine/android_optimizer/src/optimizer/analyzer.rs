//! Turns a list of running apps into transparent, explainable
//! recommendations. Deliberately simple/rule-based rather than a black box -
//! every recommendation carries a human-readable `reason` so the UI can show
//! it directly (see the "Reason: Not playing media / No foreground service"
//! example from the design notes).

use crate::models::{AppInfo, Recommendation, RecommendedAction};
use crate::optimizer::rules::RuleSet;

pub fn analyze(apps: &[AppInfo], rules: &RuleSet) -> Vec<Recommendation> {
    apps.iter().map(|app| analyze_one(app, rules)).collect()
}

fn analyze_one(app: &AppInfo, rules: &RuleSet) -> Recommendation {
    if let Some(reason) = rules.protection_reason(&app.package_name) {
        return Recommendation {
            package_name: app.package_name.clone(),
            action: RecommendedAction::DoNotStop,
            reason: reason.to_string(),
        };
    }

    if !app.is_running {
        return Recommendation {
            package_name: app.package_name.clone(),
            action: RecommendedAction::NoActionNeeded,
            reason: "App is not currently running".to_string(),
        };
    }

    if app.has_foreground_service {
        return Recommendation {
            package_name: app.package_name.clone(),
            action: RecommendedAction::DoNotStop,
            reason: "Has an active foreground service (e.g. media, download, navigation)"
                .to_string(),
        };
    }

    let rss = app.rss_kb.unwrap_or(0);
    if rss >= rules.safe_to_close_rss_threshold_kb {
        return Recommendation {
            package_name: app.package_name.clone(),
            action: RecommendedAction::SafeToForceStop,
            reason: format!(
                "Using {} MB in the background with no active foreground service",
                rss / 1024
            ),
        };
    }

    Recommendation {
        package_name: app.package_name.clone(),
        action: RecommendedAction::NoActionNeeded,
        reason: "Running, but memory usage is within normal range".to_string(),
    }
}
