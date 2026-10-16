# DriveCoach Starter Kit

Everything here is synthetic. No real people, cars or lessons.

```
sample_data/
  lessons/<car_id>/<date>/<lesson_id>.csv   16 lesson files recorded by the in-car boxes
  students.csv                              student records (contains personal data)
  rules_v1.json                             evaluation rules, version v1
terraform/                                  starter infrastructure from the previous engineer
scripts/
  upload_lessons.sh                         uploads the lesson files to S3 in field order
  upload_order.txt                          the order used by the script
```

The starter infrastructure and scripts are provided as is.
Treat them the way you would treat code inherited from an engineer who has left the team.

See the assignment document for the full task.
