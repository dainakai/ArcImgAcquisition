// ImageJ 1.x: reproduce the README measurement from sample_minip.bmp.
// Pass the absolute BMP path as a macro argument, or choose the file interactively.
input = getArgument();
if (input == "") input = File.openDialog("Open sample_minip.bmp");
open(input);
run("Set Scale...", "distance=1 known=2.74 unit=um");
run("Set Measurements...", "area feret's redirect=None decimal=3");
setAutoThreshold("Minimum");
getThreshold(lo, hi);
print("ImageJ " + getVersion() + ": Minimum threshold = " + lo + "-" + hi);
run("Convert to Mask");
run("Analyze Particles...", "size=100-Infinity circularity=0.00-1.00 show=Outlines display clear exclude");
saveAs("Results", File.getParent(input) + "/Results.csv");
run("Distribution...", "parameter=Feret or=10 and=0-100");
print("Particles = " + nResults);
