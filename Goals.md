Goals

The goals for this repo are the following:
This will be a streamlit app hosted on railway
The idea of this app is to give students, during a robotics class activity, a simple way to log the behavior of the robot.

During the activity they will be assigned a robot which has 2 sonar sensors it uses to do obstacle avoidance (it compares the left and right distance measurements and makes a decision on whether to turn left or right). The robot runs either the "green" or "red" algorithm. These are a version of kinesis and taxis, respectively.  In the taxis version, the left and right are not compared but, if I remember correctly, I just take the min distance across the 2 sensors to decided whether to turn and the direction (left vs right) is random.

A second way in which the robots differ is their "ear" placement. Either they are angled out or they are aligned. This tests whether the position of the ears matters.

During the activity the students will run their robot in an arena and count the number of collisions. They will do this for each of the 4 combinations:

[taxis or kinesis] x [aligned or angled]

The app we are building here should allow the following:
- The student should be able to select which condition they are running
- Then they click start, timer starts counting down
- and they get a number of buttons for diffirent accurances (hit wall, hit other robot, stuck). They can click these buttons to log an occurence.
- At the end of the time, they get the option to submit their data to the database.