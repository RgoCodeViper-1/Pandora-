/// Like assert_eq, but with readable diff output for long strings.
/// Adapted from ripgrep grep-searcher macros.rs — kept identical because
/// the test-helper contract is the same across both codebases.
#[cfg(test)]
#[macro_export]
macro_rules! assert_eq_printed {
    ($expected:expr, $got:expr, $($tt:tt)*) => {
        let expected = &*$expected;
        let got      = &*$got;
        let label    = format!($($tt)*);
        if expected != got {
            panic!(
                "\nprinted outputs differ! (label: {})\n\
                 expected:\n\
                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n\
                 {}\n\
                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n\
                 got:\n\
                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n\
                 {}\n\
                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n",
                label, expected, got
            );
        }
    };
}

/// Quick intent-match assertion used in unit tests inside patterns/*.rs.
/// Checks that the top-level intent field matches `$expected_intent`.
#[cfg(test)]
#[macro_export]
macro_rules! assert_intent {
    ($result:expr, $expected_intent:expr) => {
        match &$result {
            Some(r) => {
                let got = r.get("intent").and_then(|v| v.as_str()).unwrap_or("");
                assert_eq!(
                    got, $expected_intent,
                    "intent mismatch: expected {:?}, got {:?}",
                    $expected_intent, got
                );
            }
            None => panic!("expected intent {:?}, got None", $expected_intent),
        }
    };
}

/// Assert that a field inside the `entities` map equals a given value.
#[cfg(test)]
#[macro_export]
macro_rules! assert_entity {
    ($result:expr, $key:expr, $value:expr) => {
        match &$result {
            Some(r) => {
                let entities = r.get("entities").expect("no entities field");
                let got = entities
                    .get($key)
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                assert_eq!(
                    got, $value,
                    "entity[{}] mismatch: expected {:?}, got {:?}",
                    $key, $value, got
                );
            }
            None => panic!("expected entity {}={:?}, got None", $key, $value),
        }
    };
}
