use morlet_core::morlet::extract;
use serde_json::{json, Value};
use std::path::Path;

fn compare(actual: &Value, expected: &Value, path: &str) {
    match expected {
        Value::Number(e) => {
            let e = e.as_f64().unwrap();
            let a = actual.as_f64().unwrap();
            assert!(
                a.is_finite() && (a - e).abs() <= 1e-10 + 1e-9 * e.abs(),
                "{path}: got {a}, expected {e}"
            );
        }
        Value::String(e) => assert_eq!(actual.as_str().unwrap(), e, "{path}"),
        Value::Array(e) => {
            let a = actual.as_array().unwrap();
            assert_eq!(a.len(), e.len(), "{path}");
            for (i, (a, e)) in a.iter().zip(e).enumerate() {
                compare(a, e, &format!("{path}[{i}]"));
            }
        }
        Value::Object(e) => {
            let a = actual.as_object().unwrap();
            assert_eq!(a.len(), e.len(), "{path}");
            for (k, e) in e {
                compare(&a[k], e, &format!("{path}.{k}"));
            }
        }
        _ => panic!("unsupported fixture value"),
    }
}
#[test]
fn shared_go_rust_fixtures() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/morlet-energy-v1");
    let mut paths: Vec<_> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|s| s == "json"))
        .collect();
    paths.sort();
    assert_eq!(paths.len(), 8);
    for path in paths {
        let fixture: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        let samples: Vec<f64> = fixture["capture"]["samples"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect();
        let signature = extract(
            &samples,
            fixture["capture"]["sample_rate_hz"].as_f64().unwrap(),
        )
        .unwrap();
        compare(
            &serde_json::to_value(&signature).unwrap(),
            &fixture["signature"],
            path.to_str().unwrap(),
        );
        compare(
            &json!(signature.features()),
            &fixture["features"],
            "features",
        );
    }
}
