function [servomodel] = servo_model(input,omega,ratelim,samplefreq)
N = length(input);
servomodel = zeros(N,1);

for i = 2:N
    servomodel(i) = servomodel(i-1) + omega*(input(i)-servomodel(i-1))/samplefreq;
    if (servomodel(i) - servomodel(i-1)) > ratelim/samplefreq
        servomodel(i) = servomodel(i-1) + ratelim/samplefreq;
    end
    if (servomodel(i) - servomodel(i-1)) < -ratelim/samplefreq
        servomodel(i) = servomodel(i-1) - ratelim/samplefreq;
    end
end

end