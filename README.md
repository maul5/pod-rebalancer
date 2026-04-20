# 🚀 pod-rebalancer

Kubernetes(k3s) 환경에서 노드 리소스 상태를 기반으로 Pod를 자동 재배치하여 클러스터의 자원 균형을 유지하는 Rebalancer Controller입니다.

## 🎯 주요 기능

- 노드 CPU / Memory 기반 점수 계산
- 리소스 여유 노드로 Pod 자동 재배치
- 5분 주기 자동 실행 (CronJob)
- Replica 2 이상 대상만 안전하게 처리
- 순차적 Pod 이동
- 텔레그램 알림 전송
- Timeout 발생 시 skip 후 계속 진행

## ⚙️ 동작 방식

1. `kubectl top nodes`로 노드 리소스 조회
2. `score = (100 - CPU%) + (100 - Memory%)` 계산
3. 최저 점수 노드 선택
4. worst node `cordon`
5. Deployment 기반 Pod 중 `replicas >= 2` 후보 선택
6. Pod를 하나씩 삭제
7. 대체 Pod Ready 상태를 최대 60초 대기
8. 성공 시 다음 Pod 진행, timeout 시 skip
9. `MAX_MOVE = max(1, min(2, node_count // 3))`까지 반복
10. `uncordon` 후 Telegram 전송

## 📁 프로젝트 구조

```text
pod-rebalancer/
├── app/
│   ├── main.py
│   ├── scheduler.py
│   ├── k8s.py
│   ├── notifier.py
│   └── config.py
├── k8s/
│   ├── cronjob.yaml
│   ├── rbac.yaml
│   ├── configmap.yaml
│   └── secret.example.yaml
├── Dockerfile
├── requirements.txt
└── README.md
```

## ☸️ Kubernetes 배포

```bash
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/cronjob.yaml
```

## 🐳 Harbor 이미지

- 기본 이미지 경로: `harbor.local/library/pod-rebalancer`
- GitHub Actions에서 `main` 브랜치 푸시 또는 태그 푸시 시 Harbor에 자동 업로드되도록 설정
- 필요한 GitHub Secrets:
  - `HARBOR_USERNAME`
  - `HARBOR_PASSWORD`

## 🚚 원격 운영 스크립트

- `scripts/push_to_harbor.py`: `192.168.219.211` 에 SSH 접속 후 Docker 빌드 및 Harbor 푸시
- `scripts/deploy_to_k3s.py`: 동일 서버에서 `kubectl apply` 로 `k8s/*.yaml` 배포
